use anyhow::{Context, Result};
use cadence::{Counted, CountedExt, StatsdClient, Timed, UdpMetricSink};
use clap::{Parser, Subcommand};
use deltalake::arrow::array::{RecordBatch, StringArray};
use deltalake::arrow::datatypes::Schema;
use deltalake::kernel::{DataType as DeltaDataType, StructField};
use deltalake::writer::{DeltaWriter, RecordBatchWriter};
use deltalake::{ensure_table_uri, DeltaTable, DeltaTableBuilder};
use jsonschema::Draft;
use rdkafka::config::ClientConfig;
use rdkafka::consumer::{CommitMode, Consumer, StreamConsumer};
use rdkafka::message::{Header, OwnedHeaders};
use rdkafka::producer::{FutureProducer, FutureRecord};
use rdkafka::util::Timeout;
use rdkafka::{Message, Offset, TopicPartitionList};
use redis::{aio::ConnectionManager, AsyncCommands};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::net::UdpSocket;
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tracing::{error, info, warn};

/// ScrapyMetrics tracks spider metrics in Redis following Scrapy signal patterns
#[derive(Clone)]
struct ScrapyMetrics {
    redis: ConnectionManager,
    spider_name: String,
}

impl ScrapyMetrics {
    async fn new(redis_url: &str, spider_name: String) -> Result<Self> {
        let client = redis::Client::open(redis_url)?;
        let redis = ConnectionManager::new(client).await?;
        Ok(Self { redis, spider_name })
    }

    /// Signal: spider_opened - Initialize crawl session
    async fn spider_opened(&mut self) -> Result<()> {
        let start_time = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
        self.redis
            .hset::<_, _, _, ()>(&format!("stats:{}:summary", self.spider_name), "start_time", start_time)
            .await?;
        info!("Spider opened: {}", self.spider_name);
        Ok(())
    }

    /// Signal: response_received - Track HTTP status codes and response latency
    async fn response_received(&mut self, status_code: u16) -> Result<()> {
        self.redis
            .hincr::<_, _, _, i64>(&format!("stats:{}:status_codes", self.spider_name), status_code.to_string(), 1)
            .await?;
        Ok(())
    }

    /// Signal: item_scraped - Primary throughput metric
    async fn item_scraped(&mut self) -> Result<()> {
        self.redis
            .hincr::<_, _, _, i64>(&format!("stats:{}:summary", self.spider_name), "items_scraped", 1)
            .await?;

        // Add to time-series for graphing
        let timestamp = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
        let current_count: i64 = self.redis
            .hget(&format!("stats:{}:summary", self.spider_name), "items_scraped")
            .await
            .unwrap_or(0);

        self.redis
            .zadd::<_, _, _, ()>(&format!("stats:{}:timeseries:items_scraped", self.spider_name), current_count, timestamp)
            .await?;
        Ok(())
    }

    /// Signal: item_dropped - Monitor data quality
    async fn item_dropped(&mut self, reason: &str) -> Result<()> {
        self.redis
            .hincr::<_, _, _, i64>(&format!("stats:{}:summary", self.spider_name), "items_dropped", 1)
            .await?;

        // Track drop reason
        self.redis
            .hincr::<_, _, _, i64>(&format!("stats:{}:drop_reasons", self.spider_name), reason, 1)
            .await?;
        Ok(())
    }

    /// Signal: spider_error - Flag critical failures
    async fn spider_error(&mut self, error_type: &str, error_msg: &str) -> Result<()> {
        self.redis
            .hincr::<_, _, _, i64>(&format!("stats:{}:summary", self.spider_name), "total_errors", 1)
            .await?;

        // Track error type
        self.redis
            .hincr::<_, _, _, i64>(&format!("stats:{}:error_types", self.spider_name), error_type, 1)
            .await?;

        // Store recent error (capped list of 100)
        let error_entry = format!("{}: {}", error_type, error_msg);
        self.redis
            .lpush::<_, _, ()>(&format!("stats:{}:errors", self.spider_name), &error_entry)
            .await?;
        self.redis
            .ltrim::<_, ()>(&format!("stats:{}:errors", self.spider_name), 0, 99)
            .await?;
        Ok(())
    }

    /// Signal: request_dropped - Track dropped requests
    #[allow(dead_code)]
    async fn request_dropped(&mut self) -> Result<()> {
        self.redis
            .hincr::<_, _, _, i64>(&format!("stats:{}:summary", self.spider_name), "requests_dropped", 1)
            .await?;
        Ok(())
    }

    /// Signal: spider_closed - Finalize crawl session
    #[allow(dead_code)]
    async fn spider_closed(&mut self, reason: &str) -> Result<()> {
        let finish_time = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
        self.redis
            .hset::<_, _, _, ()>(&format!("stats:{}:summary", self.spider_name), "finish_time", finish_time)
            .await?;
        self.redis
            .hset::<_, _, _, ()>(&format!("stats:{}:summary", self.spider_name), "finish_reason", reason)
            .await?;
        info!("Spider closed: {} (reason: {})", self.spider_name, reason);
        Ok(())
    }
}

/// Fields every ingested message must carry as non-empty strings. Shared with the
/// Python producer contract (`src/core/ingest_contract.py`, checked by
/// `tests/unit/test_ingest_field_contract.py`) (#531).
const REQUIRED_INGEST_FIELDS: [&str; 3] = ["url", "scraped_at_utc", "spider_name"];

/// One validated message, typed. Required fields are never defaulted: a message
/// that lacks one is rejected instead of being written with an empty string (#531).
#[derive(Debug, Clone, PartialEq)]
struct IngestRow {
    url: String,
    title: Option<String>,
    content: Option<String>,
    scraped_at_utc: String,
    spider_name: String,
    pipeline_version: Option<String>,
    /// `YYYY-MM-DD` prefix of `scraped_at_utc`, used for the `date` partition.
    date: Option<String>,
}

fn required_str(record: &Value, field: &str) -> std::result::Result<String, String> {
    match record.get(field) {
        Some(Value::String(s)) if !s.trim().is_empty() => Ok(s.clone()),
        Some(Value::String(_)) => Err(format!("required field `{field}` is empty")),
        Some(Value::Null) | None => Err(format!("required field `{field}` is missing")),
        Some(_) => Err(format!("required field `{field}` is not a string")),
    }
}

fn optional_str(record: &Value, field: &str) -> Option<String> {
    record.get(field).and_then(|v| v.as_str()).map(str::to_string)
}

/// Convert a JSON message into an [`IngestRow`], rejecting (not coercing) any
/// missing / empty / non-string required field.
fn parse_ingest_row(record: &Value) -> std::result::Result<IngestRow, String> {
    let url = required_str(record, "url")?;
    let scraped_at_utc = required_str(record, "scraped_at_utc")?;
    let spider_name = required_str(record, "spider_name")?;
    let date = scraped_at_utc.get(0..10).map(str::to_string);
    Ok(IngestRow {
        url,
        title: optional_str(record, "title"),
        content: optional_str(record, "content"),
        scraped_at_utc,
        spider_name,
        pipeline_version: optional_str(record, "pipeline_version"),
        date,
    })
}

/// Build the JSON schema for scraped items to validate incoming messages
/// A message the ingestor refuses to write to Delta. It goes to the DLQ (#544).
#[derive(Debug, Clone, PartialEq)]
struct Rejection {
    /// Stable machine-readable reason; also the StatsD `errors.<reason>` suffix.
    reason: &'static str,
    detail: String,
}

/// Validate one Kafka payload: Ok(row) to buffer for Delta, or Err(rejection) for the DLQ.
fn classify(
    payload: Option<&[u8]>,
    validator: &jsonschema::Validator,
) -> std::result::Result<IngestRow, Rejection> {
    let bytes = payload.ok_or_else(|| Rejection {
        reason: "empty_payload",
        detail: "message has no payload".to_string(),
    })?;
    let value: Value = serde_json::from_slice(bytes).map_err(|e| Rejection {
        reason: "parse_failed",
        detail: e.to_string(),
    })?;
    let errors: Vec<String> = validator
        .iter_errors(&value)
        .map(|e| format!("{} at {}", e, e.instance_path))
        .collect();
    if !errors.is_empty() {
        return Err(Rejection {
            reason: "schema_validation_failed",
            detail: errors.join("; "),
        });
    }
    // Never write a defaulted ("") required field (#531).
    parse_ingest_row(&value).map_err(|detail| Rejection {
        reason: "missing_required_field",
        detail,
    })
}

const DLQ_DETAIL_MAX: usize = 4000;

/// Headers on every dead-lettered message: why it was rejected and where it came from,
/// so it can be replayed to the source topic after a schema fix.
fn dlq_headers(
    rejection: &Rejection,
    source_topic: &str,
    partition: i32,
    offset: i64,
    rejected_at_ms: u128,
) -> Vec<(String, String)> {
    let mut detail = rejection.detail.clone();
    if detail.len() > DLQ_DETAIL_MAX {
        let mut cut = DLQ_DETAIL_MAX;
        while !detail.is_char_boundary(cut) {
            cut -= 1;
        }
        detail.truncate(cut);
    }
    vec![
        ("dlq.reason".to_string(), rejection.reason.to_string()),
        ("dlq.error".to_string(), detail),
        ("dlq.source.topic".to_string(), source_topic.to_string()),
        ("dlq.source.partition".to_string(), partition.to_string()),
        ("dlq.source.offset".to_string(), offset.to_string()),
        ("dlq.rejected_at_ms".to_string(), rejected_at_ms.to_string()),
        ("dlq.producer".to_string(), "kafka-delta-ingest".to_string()),
    ]
}

/// Durable dead-letter producer (#544). Each send waits for broker acks
/// (acks=all, idempotent). The caller fails closed if it can't deliver.
struct DeadLetterQueue {
    producer: FutureProducer,
    topic: String,
    timeout: Duration,
    max_attempts: u32,
}

impl DeadLetterQueue {
    fn new(brokers: &str, topic: &str, max_attempts: u32) -> Result<Self> {
        Self::with_timeout(brokers, topic, max_attempts, Duration::from_secs(30))
    }

    fn with_timeout(brokers: &str, topic: &str, max_attempts: u32, timeout: Duration) -> Result<Self> {
        let producer: FutureProducer = ClientConfig::new()
            .set("bootstrap.servers", brokers)
            .set("acks", "all")
            .set("enable.idempotence", "true")
            .set("message.timeout.ms", timeout.as_millis().to_string())
            .create()
            .context("Failed to create dead-letter producer")?;
        Ok(Self {
            producer,
            topic: topic.to_string(),
            timeout,
            max_attempts: max_attempts.max(1),
        })
    }

    async fn send(&self, payload: &[u8], key: Option<&[u8]>, headers: &[(String, String)]) -> Result<()> {
        let mut last_err = String::new();
        for attempt in 1..=self.max_attempts {
            let mut owned = OwnedHeaders::new();
            for (k, v) in headers {
                owned = owned.insert(Header { key: k.as_str(), value: Some(v.as_bytes()) });
            }
            let mut record = FutureRecord::<[u8], [u8]>::to(&self.topic).payload(payload).headers(owned);
            if let Some(k) = key {
                record = record.key(k);
            }
            match self.producer.send(record, Timeout::After(self.timeout)).await {
                Ok(_) => return Ok(()),
                Err((e, _)) => {
                    last_err = e.to_string();
                    warn!("DLQ produce to {} failed (attempt {}/{}): {}", self.topic, attempt, self.max_attempts, e);
                    if attempt < self.max_attempts {
                        tokio::time::sleep(write_retry_backoff(attempt)).await;
                    }
                }
            }
        }
        Err(anyhow::anyhow!(
            "DLQ produce to {} failed after {} attempts: {}",
            self.topic,
            self.max_attempts,
            last_err
        ))
    }
}

fn build_scraped_item_schema() -> Value {
    json!({
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "required": REQUIRED_INGEST_FIELDS,
        "properties": {
            "url": {
                "type": "string",
                "minLength": 1,
                "description": "The URL that was scraped"
            },
            "title": {
                "type": ["string", "null"],
                "description": "Page title (optional)"
            },
            "content": {
                "type": ["string", "null"],
                "description": "Page content (optional)"
            },
            "scraped_at_utc": {
                "type": "string",
                "pattern": "^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}",
                "description": "UTC timestamp in ISO 8601 format"
            },
            "spider_name": {
                "type": "string",
                "minLength": 1,
                "description": "Name of the spider that collected this data"
            },
            "pipeline_version": {
                "type": ["string", "null"],
                "description": "Version of the scraping pipeline (optional)"
            }
        },
        "additionalProperties": true
    })
}

#[derive(Parser)]
#[command(name = "kafka-delta-ingest")]
#[command(about = "High-performance Kafka to Delta Lake ingestor", long_about = None)]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Ingest messages from Kafka topic to Delta Lake table
    Ingest {
        /// Kafka topic to consume from
        topic: String,

        /// Delta Lake table path on the local filesystem (e.g., /path/to/delta-table).
        /// s3:// paths need the deltalake `s3` feature, which this build does not enable.
        #[arg(value_name = "TABLE_PATH")]
        table_path: String,

        /// Kafka bootstrap servers
        #[arg(long, default_value = "localhost:9092")]
        kafka: String,

        /// Consumer group ID
        #[arg(long, default_value = "kafka-delta-ingest")]
        app_id: String,

        /// Auto offset reset strategy
        #[arg(long, default_value = "earliest")]
        auto_offset_reset: String,

        /// Maximum allowed latency in seconds before forcing a batch write
        #[arg(long, default_value = "300")]
        allowed_latency: u64,

        /// Maximum messages per batch
        #[arg(long, default_value = "1000")]
        max_messages_per_batch: usize,

        /// Partition transform (e.g., 'date: substr(scraped_at_utc, `0`, `10`)')
        #[arg(long)]
        transform: Option<String>,

        /// Delta write attempts per batch (exponential backoff) before exiting
        /// without committing offsets, so the batch is re-consumed on restart
        #[arg(long, default_value = "5")]
        max_write_attempts: u32,

        /// Dead-letter topic for rejected (unparseable / schema-invalid) messages (#544)
        #[arg(long, default_value = "scraped-items-dlq")]
        dlq_topic: String,
    },
}

#[tokio::main]
async fn main() -> Result<()> {
    // Initialize tracing
    tracing_subscriber::fmt()
        .with_env_filter(
            std::env::var("RUST_LOG").unwrap_or_else(|_| "info".to_string()),
        )
        .init();

    // Load environment variables
    dotenv::dotenv().ok();

    let cli = Cli::parse();

    match cli.command {
        Commands::Ingest {
            topic,
            table_path,
            kafka,
            app_id,
            auto_offset_reset,
            allowed_latency,
            max_messages_per_batch,
            transform,
            max_write_attempts,
            dlq_topic,
        } => {
            ingest(
                &topic,
                &table_path,
                &kafka,
                &app_id,
                &auto_offset_reset,
                allowed_latency,
                max_messages_per_batch,
                transform,
                max_write_attempts,
                &dlq_topic,
            )
            .await?;
        }
    }

    Ok(())
}

async fn ingest(
    topic: &str,
    table_path: &str,
    kafka_brokers: &str,
    app_id: &str,
    auto_offset_reset: &str,
    allowed_latency: u64,
    max_messages_per_batch: usize,
    transform: Option<String>,
    max_write_attempts: u32,
    dlq_topic: &str,
) -> Result<()> {
    info!("Starting Kafka to Delta Lake ingestor");
    info!("Topic: {}", topic);
    info!("Table path: {}", table_path);
    info!("Kafka brokers: {}", kafka_brokers);
    info!("App ID: {}", app_id);

    // Build and compile JSON schema for validation
    let schema_def = build_scraped_item_schema();
    let schema_validator = jsonschema::options()
        .with_draft(Draft::Draft7)
        .build(&schema_def)
        .map_err(|e| anyhow::anyhow!("Failed to compile JSON schema: {e}"))?;
    info!("Schema validation enabled - all messages will be validated against the schema");

    // Initialize StatsD client for metrics
    let statsd_host = std::env::var("STATSD_HOST").unwrap_or_else(|_| "localhost".to_string());
    let statsd_port = std::env::var("STATSD_PORT").unwrap_or_else(|_| "9125".to_string());
    let socket = UdpSocket::bind("0.0.0.0:0")?;
    socket.set_nonblocking(true)?;
    let sink = UdpMetricSink::from(&format!("{}:{}", statsd_host, statsd_port), socket)?;
    let metrics = StatsdClient::from_sink("kafka_delta_ingest", sink);

    // Initialize Redis-based Scrapy metrics
    let redis_url = std::env::var("REDIS_URL").unwrap_or_else(|_| "redis://localhost:6379".to_string());
    let spider_name = format!("{}_spider", topic.replace('-', "_"));
    let mut scrapy_metrics = ScrapyMetrics::new(&redis_url, spider_name).await?;

    // Signal: spider_opened
    scrapy_metrics.spider_opened().await?;

    // Create Kafka consumer.
    //
    // Delivery semantics (#282): AT-LEAST-ONCE. Offsets are committed manually,
    // only after the batch containing those messages has been committed to
    // Delta. A failed write is retried with backoff; if it still fails the
    // process exits WITHOUT committing, so the batch is re-consumed after
    // restart. A crash between the Delta commit and the offset commit can
    // re-deliver (duplicate) a batch, but never skips one.
    let consumer: StreamConsumer = ClientConfig::new()
        .set("bootstrap.servers", kafka_brokers)
        .set("group.id", app_id)
        .set("auto.offset.reset", auto_offset_reset)
        .set("enable.auto.commit", "false")
        .create()
        .context("Failed to create Kafka consumer")?;

    consumer
        .subscribe(&[topic])
        .context("Failed to subscribe to topic")?;

    // Rejected messages are produced here before their offsets can be committed (#544).
    let dlq = DeadLetterQueue::new(kafka_brokers, dlq_topic, max_write_attempts)?;
    info!("Dead-letter topic: {}", dlq_topic);

    info!("Successfully connected to Kafka and subscribed to topic: {}", topic);

    // Parse partition transform if provided
    let partition_column = if let Some(transform_str) = &transform {
        // Expected format: "date: substr(scraped_at_utc, `0`, `10`)"
        // Extract the partition column name (before the colon)
        let parts: Vec<&str> = transform_str.split(':').collect();
        if parts.len() >= 1 {
            let col_name = parts[0].trim();
            info!("Partitioning enabled on column: {}", col_name);
            Some(col_name.to_string())
        } else {
            warn!("Invalid transform format: {}. Expected format: 'column_name: expression'", transform_str);
            None
        }
    } else {
        None
    };

    // Load or create Delta table (mutable: each commit advances its snapshot)
    let mut delta_table = load_or_create_table(table_path, partition_column.as_deref()).await?;
    let schema: Schema = delta_table.snapshot()?.snapshot().arrow_schema().as_ref().clone();

    info!("Delta table loaded/created successfully");
    info!("Schema: {:?}", schema);

    let mut buffer: Vec<IngestRow> = Vec::new();
    let mut last_write = std::time::Instant::now();
    // Next offset to commit per (topic, partition): every message seen so far,
    // including deliberately dropped invalid ones, but only committed once the
    // buffered batch has reached Delta.
    let mut pending_offsets = PendingOffsets::default();

    loop {
        match consumer.recv().await {
            Ok(message) => {
                pending_offsets.record(message.topic(), message.partition(), message.offset());
                match classify(message.payload(), &schema_validator) {
                    Ok(row) => {
                        // Signal: response_received - Track HTTP status (default 200 for successful parse)
                        scrapy_metrics.response_received(200).await.ok();
                        buffer.push(row);
                        metrics.incr("messages.received").ok();
                    }
                    Err(rejection) => {
                        warn!(
                            "Rejected message {}/{}@{} ({}): {}",
                            message.topic(),
                            message.partition(),
                            message.offset(),
                            rejection.reason,
                            rejection.detail
                        );
                        metrics.incr(&format!("errors.{}", rejection.reason)).ok();
                        scrapy_metrics.item_dropped(rejection.reason).await.ok();
                        scrapy_metrics.spider_error(rejection.reason, &rejection.detail).await.ok();

                        // #544: never drop silently. The offset is already recorded, so it is
                        // committed with the next batch; produce to the DLQ first and fail
                        // closed (exit without committing) if that is impossible.
                        let rejected_at_ms = SystemTime::now()
                            .duration_since(UNIX_EPOCH)
                            .map(|d| d.as_millis())
                            .unwrap_or(0);
                        let headers = dlq_headers(
                            &rejection,
                            message.topic(),
                            message.partition(),
                            message.offset(),
                            rejected_at_ms,
                        );
                        if let Err(e) = dlq.send(message.payload().unwrap_or(&[]), message.key(), &headers).await {
                            metrics.incr("errors.dlq_produce_failed").ok();
                            return Err(e.context(
                                "Dead-letter produce failed; exiting without committing offsets",
                            ));
                        }
                        metrics.incr("messages.dead_lettered").ok();
                    }
                }

                            // Check if we should write the batch
                            let should_write = buffer.len() >= max_messages_per_batch
                                || last_write.elapsed() >= Duration::from_secs(allowed_latency);

                            if should_write {
                                // The `date` partition value is derived in parse_ingest_row.
                                info!("Writing batch of {} messages to Delta Lake", buffer.len());

                                let mut attempt: u32 = 0;
                                loop {
                                    attempt += 1;
                                    match write_batch(&mut delta_table, &schema, &buffer, &metrics, &mut scrapy_metrics).await {
                                        Ok(()) => {
                                            info!("Successfully wrote {} records", buffer.len());
                                            metrics.count("records.written", buffer.len() as i64).ok();
                                            buffer.clear();
                                            last_write = std::time::Instant::now();
                                            // Only now is it safe to advance the group's offsets.
                                            match pending_offsets.commit(&consumer) {
                                                Ok(()) => metrics.incr("offsets.committed").ok(),
                                                Err(e) => {
                                                    // Data is in Delta; keep the offsets and retry
                                                    // the commit next batch (worst case: duplicates).
                                                    warn!("Offset commit failed after Delta write: {}", e);
                                                    metrics.incr("errors.offset_commit_failed").ok()
                                                }
                                            };
                                            break;
                                        }
                                        Err(e) => {
                                            error!("Failed to write batch (attempt {}/{}): {}", attempt, max_write_attempts, e);
                                            metrics.incr("errors.write_failed").ok();

                                            // Signal: spider_error
                                            scrapy_metrics.spider_error("write_failed", &e.to_string()).await.ok();

                                            if attempt >= max_write_attempts {
                                                // Do NOT commit: exit so the uncommitted batch is
                                                // re-consumed after restart instead of being skipped.
                                                return Err(e.context(format!(
                                                    "Delta write failed {} times; exiting without committing offsets",
                                                    attempt
                                                )));
                                            }
                                            tokio::time::sleep(write_retry_backoff(attempt)).await;
                                        }
                                    }
                                }
                            }
            }
            Err(e) => {
                warn!("Kafka error: {}", e);
                metrics.incr("errors.kafka").ok();

                // Signal: spider_error
                scrapy_metrics.spider_error("kafka_error", &e.to_string()).await.ok();
            }
        }
    }
}

/// Exponential backoff between Delta write attempts: 1s, 2s, 4s ... capped at 30s.
fn write_retry_backoff(attempt: u32) -> Duration {
    Duration::from_secs((1u64 << attempt.saturating_sub(1).min(5)).min(30))
}

/// Next offset to commit for every (topic, partition) seen since the last commit.
#[derive(Default, Debug)]
struct PendingOffsets {
    next: HashMap<(String, i32), i64>,
}

impl PendingOffsets {
    /// Record a consumed message; the committed offset is the *next* one to read.
    fn record(&mut self, topic: &str, partition: i32, offset: i64) {
        let entry = self.next.entry((topic.to_string(), partition)).or_insert(offset + 1);
        if offset + 1 > *entry {
            *entry = offset + 1;
        }
    }

    fn to_list(&self) -> Result<TopicPartitionList> {
        let mut tpl = TopicPartitionList::new();
        for ((topic, partition), offset) in &self.next {
            tpl.add_partition_offset(topic, *partition, Offset::Offset(*offset))?;
        }
        Ok(tpl)
    }

    /// Synchronously commit everything recorded; cleared only on success.
    fn commit(&mut self, consumer: &StreamConsumer) -> Result<()> {
        if self.next.is_empty() {
            return Ok(());
        }
        consumer.commit(&self.to_list()?, CommitMode::Sync)?;
        self.next.clear();
        Ok(())
    }
}

async fn load_or_create_table(table_path: &str, partition_column: Option<&str>) -> Result<DeltaTable> {
    // Try to load existing table
    let table_url = ensure_table_uri(table_path).context("Invalid Delta table location")?;
    match DeltaTableBuilder::from_uri(table_url)?.load().await {
        Ok(table) => {
            info!("Loaded existing Delta table from: {}", table_path);
            Ok(table)
        }
        Err(_) => {
            info!("Creating new Delta table at: {}", table_path);

            // Define schema for scraped items using Delta kernel types
            let mut fields = vec![
                StructField::new("url", DeltaDataType::STRING, false),
                StructField::new("title", DeltaDataType::STRING, true),
                StructField::new("content", DeltaDataType::STRING, true),
                StructField::new("scraped_at_utc", DeltaDataType::STRING, false),
                StructField::new("spider_name", DeltaDataType::STRING, false),
                StructField::new("pipeline_version", DeltaDataType::STRING, true),
            ];

            // Add date column for partitioning if specified
            if partition_column.is_some() {
                fields.push(StructField::new("date", DeltaDataType::STRING, true));
            }

            // If partitioning is enabled, add the partition column to schema if needed
            let mut builder = deltalake::operations::create::CreateBuilder::new()
                .with_location(table_path)
                .with_columns(fields);

            // Add partition columns if specified
            if let Some(part_col) = partition_column {
                info!("Creating table with partition column: {}", part_col);
                builder = builder.with_partition_columns(vec![part_col]);
            }

            builder
                .await
                .context("Failed to create Delta table")
        }
    }
}

async fn write_batch(
    table: &mut DeltaTable,
    schema: &Schema,
    records: &[IngestRow],
    metrics: &StatsdClient,
    scrapy_metrics: &mut ScrapyMetrics,
) -> Result<()> {
    if records.is_empty() {
        return Ok(());
    }

    // Convert JSON records to Arrow RecordBatch
    let mut urls = Vec::new();
    let mut titles = Vec::new();
    let mut contents = Vec::new();
    let mut scraped_ats = Vec::new();
    let mut spider_names = Vec::new();
    let mut pipeline_versions = Vec::new();
    let mut dates = Vec::new();

    // Check if schema includes date column (for partitioning)
    let has_date_column = schema.fields().iter().any(|f| f.name() == "date");

    for record in records {
        urls.push(record.url.as_str());
        titles.push(record.title.as_deref());
        contents.push(record.content.as_deref());
        scraped_ats.push(record.scraped_at_utc.as_str());
        spider_names.push(record.spider_name.as_str());
        pipeline_versions.push(record.pipeline_version.as_deref());

        if has_date_column {
            dates.push(record.date.as_deref());
        }

        // Signal: item_scraped for each successfully written item
        scrapy_metrics.item_scraped().await.ok();
    }

    // Build column arrays - add date column if schema includes it
    let mut columns: Vec<Arc<dyn deltalake::arrow::array::Array>> = vec![
        Arc::new(StringArray::from(urls)),
        Arc::new(StringArray::from(titles)),
        Arc::new(StringArray::from(contents)),
        Arc::new(StringArray::from(scraped_ats)),
        Arc::new(StringArray::from(spider_names)),
        Arc::new(StringArray::from(pipeline_versions)),
    ];

    if has_date_column {
        columns.push(Arc::new(StringArray::from(dates)));
    }

    let batch = RecordBatch::try_new(Arc::new(schema.clone()), columns)?;

    // Write to Delta Lake. "batch.write" is a StatsD timer (ms) covering the
    // write and the commit; statsd_mapping.yml turns it into the
    // kafka_delta_ingest_batch_write_seconds histogram that the
    // SlowDeltaLakeWrites alert reads (#178).
    let write_started = std::time::Instant::now();
    let mut writer = RecordBatchWriter::for_table(table)?;
    writer.write(batch).await?;
    // Commit into the caller's table so its snapshot advances (it previously
    // committed into a throwaway clone and kept writing from a stale version).
    writer.flush_and_commit(table).await?;

    metrics.time("batch.write", write_started.elapsed()).ok();
    metrics.incr("batches.written").ok();

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pending_offsets_track_next_offset_per_partition() {
        let mut p = PendingOffsets::default();
        p.record("items", 0, 10);
        p.record("items", 0, 11);
        p.record("items", 1, 5);
        p.record("items", 0, 7); // out-of-order never moves an offset backwards
        assert_eq!(p.next[&("items".to_string(), 0)], 12);
        assert_eq!(p.next[&("items".to_string(), 1)], 6);

        let tpl = p.to_list().unwrap();
        assert_eq!(tpl.count(), 2);
        let e = tpl.find_partition("items", 0).unwrap();
        assert_eq!(e.offset(), Offset::Offset(12));
    }

    fn valid_message() -> Value {
        json!({
            "url": "https://uconn.edu/a",
            "title": "A",
            "scraped_at_utc": "2026-10-07T22:30:00.123456Z",
            "spider_name": "discovery",
            "pipeline_version": "1.0.0"
        })
    }

    #[test]
    fn parse_ingest_row_accepts_valid_message() {
        let row = parse_ingest_row(&valid_message()).unwrap();
        assert_eq!(row.url, "https://uconn.edu/a");
        assert_eq!(row.spider_name, "discovery");
        assert_eq!(row.title.as_deref(), Some("A"));
        assert_eq!(row.content, None);
        assert_eq!(row.date.as_deref(), Some("2026-10-07"));
    }

    #[test]
    fn parse_ingest_row_rejects_instead_of_coercing() {
        for field in REQUIRED_INGEST_FIELDS {
            let mut missing = valid_message();
            missing.as_object_mut().unwrap().remove(field);
            let err = parse_ingest_row(&missing).unwrap_err();
            assert!(err.contains(field) && err.contains("missing"), "{err}");

            let mut empty = valid_message();
            empty[field] = json!("  ");
            assert!(parse_ingest_row(&empty).unwrap_err().contains("empty"));

            let mut null = valid_message();
            null[field] = Value::Null;
            assert!(parse_ingest_row(&null).unwrap_err().contains("missing"));

            let mut wrong_type = valid_message();
            wrong_type[field] = json!(42);
            assert!(parse_ingest_row(&wrong_type).unwrap_err().contains("not a string"));
        }
    }

    #[test]
    fn optional_fields_may_be_absent_or_null() {
        let mut msg = valid_message();
        msg["pipeline_version"] = Value::Null;
        msg.as_object_mut().unwrap().remove("title");
        let row = parse_ingest_row(&msg).unwrap();
        assert_eq!(row.pipeline_version, None);
        assert_eq!(row.title, None);
    }

    #[test]
    fn json_schema_requires_the_shared_field_list() {
        let schema = build_scraped_item_schema();
        let required: Vec<&str> = schema["required"].as_array().unwrap().iter().map(|v| v.as_str().unwrap()).collect();
        assert_eq!(required, REQUIRED_INGEST_FIELDS.to_vec());

        let validator = jsonschema::options().with_draft(Draft::Draft7).build(&schema).unwrap();
        assert!(validator.is_valid(&valid_message()));
        let mut drifted = valid_message();
        let name = drifted.as_object_mut().unwrap().remove("spider_name").unwrap();
        drifted["spider"] = name; // name drift must be rejected, not defaulted
        assert!(!validator.is_valid(&drifted));
    }

    #[test]
    fn backoff_is_exponential_and_capped() {
        assert_eq!(write_retry_backoff(1), Duration::from_secs(1));
        assert_eq!(write_retry_backoff(2), Duration::from_secs(2));
        assert_eq!(write_retry_backoff(4), Duration::from_secs(8));
        assert_eq!(write_retry_backoff(50), Duration::from_secs(30));
    }

    // ------------------------------------------------------------ #544 DLQ
    fn validator() -> jsonschema::Validator {
        jsonschema::options()
            .with_draft(Draft::Draft7)
            .build(&build_scraped_item_schema())
            .unwrap()
    }

    #[test]
    fn classify_accepts_valid_and_rejects_each_kind() {
        let v = validator();
        let good = serde_json::to_vec(&valid_message()).unwrap();
        assert!(classify(Some(&good), &v).is_ok());

        assert_eq!(classify(None, &v).unwrap_err().reason, "empty_payload");
        assert_eq!(classify(Some(b"{not json"), &v).unwrap_err().reason, "parse_failed");

        let mut bad = valid_message();
        bad["url"] = json!(12345);
        let bad = serde_json::to_vec(&bad).unwrap();
        let rej = classify(Some(&bad), &v).unwrap_err();
        assert_eq!(rej.reason, "schema_validation_failed");
        assert!(rej.detail.contains("url"), "{}", rej.detail);
    }

    #[test]
    fn dlq_headers_carry_reason_source_and_truncated_detail() {
        let rej = Rejection { reason: "schema_validation_failed", detail: "é".repeat(5000) };
        let h: HashMap<String, String> = dlq_headers(&rej, "scraped-items", 3, 42, 1700000000000)
            .into_iter()
            .collect();
        assert_eq!(h["dlq.reason"], "schema_validation_failed");
        assert_eq!(h["dlq.source.topic"], "scraped-items");
        assert_eq!(h["dlq.source.partition"], "3");
        assert_eq!(h["dlq.source.offset"], "42");
        assert_eq!(h["dlq.rejected_at_ms"], "1700000000000");
        assert!(h["dlq.error"].len() <= DLQ_DETAIL_MAX);
    }

    /// Integration: a rejected payload lands on the DLQ topic, byte-for-byte, with headers
    /// (librdkafka in-process mock cluster, no external Kafka needed).
    #[tokio::test]
    async fn dlq_produces_payload_and_headers_to_mock_cluster() {
        use rdkafka::consumer::BaseConsumer;
        use rdkafka::message::Headers;
        use rdkafka::mocking::MockCluster;

        let cluster = MockCluster::new(1).unwrap();
        cluster.create_topic("scraped-items-dlq", 1, 1).unwrap();
        let brokers = cluster.bootstrap_servers();

        let dlq = DeadLetterQueue::with_timeout(&brokers, "scraped-items-dlq", 3, Duration::from_secs(10)).unwrap();
        let rej = Rejection { reason: "parse_failed", detail: "expected value at line 1".into() };
        let headers = dlq_headers(&rej, "scraped-items", 0, 7, 1);
        dlq.send(b"{not json", Some(b"k1"), &headers).await.unwrap();

        let consumer: BaseConsumer = ClientConfig::new()
            .set("bootstrap.servers", &brokers)
            .set("group.id", "dlq-test")
            .set("auto.offset.reset", "earliest")
            .create()
            .unwrap();
        consumer.subscribe(&["scraped-items-dlq"]).unwrap();
        let mut got = None;
        for _ in 0..100 {
            if let Some(Ok(m)) = consumer.poll(Duration::from_millis(200)) {
                got = Some(m.detach());
                break;
            }
        }
        let m = got.expect("DLQ message not delivered");
        assert_eq!(m.payload().unwrap(), b"{not json");
        assert_eq!(m.key().unwrap(), b"k1");
        let hs = m.headers().unwrap();
        let found: HashMap<String, String> = (0..hs.count())
            .map(|i| {
                let h = hs.get(i);
                (h.key.to_string(), String::from_utf8_lossy(h.value.unwrap()).to_string())
            })
            .collect();
        assert_eq!(found["dlq.reason"], "parse_failed");
        assert_eq!(found["dlq.source.offset"], "7");
    }

    /// Fail closed: if the DLQ cannot be written, send() errors (the ingest loop then exits
    /// without committing offsets) instead of dropping the message.
    #[tokio::test]
    async fn dlq_send_fails_closed_when_broker_unreachable() {
        let dlq = DeadLetterQueue::with_timeout("127.0.0.1:1", "scraped-items-dlq", 1, Duration::from_millis(500)).unwrap();
        let rej = Rejection { reason: "parse_failed", detail: "x".into() };
        let err = dlq.send(b"x", None, &dlq_headers(&rej, "t", 0, 0, 0)).await.unwrap_err();
        assert!(err.to_string().contains("DLQ produce"), "{err}");
    }
}
