use anyhow::{Context, Result};
use cadence::{Counted, CountedExt, StatsdClient, UdpMetricSink};
use clap::{Parser, Subcommand};
use deltalake::arrow::array::{RecordBatch, StringArray};
use deltalake::arrow::datatypes::Schema;
use deltalake::delta_datafusion::DataFusionMixins;
use deltalake::kernel::{DataType as DeltaDataType, StructField};
use deltalake::writer::{DeltaWriter, RecordBatchWriter};
use deltalake::{DeltaTable, DeltaTableBuilder};
use rdkafka::config::ClientConfig;
use rdkafka::consumer::{Consumer, StreamConsumer};
use rdkafka::Message;
use redis::{aio::ConnectionManager, AsyncCommands};
use serde_json::Value;
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

        /// Delta Lake table path (e.g., /path/to/delta-table or s3://bucket/path)
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
    _transform: Option<String>,
) -> Result<()> {
    info!("Starting Kafka to Delta Lake ingestor");
    info!("Topic: {}", topic);
    info!("Table path: {}", table_path);
    info!("Kafka brokers: {}", kafka_brokers);
    info!("App ID: {}", app_id);

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

    // Create Kafka consumer
    let consumer: StreamConsumer = ClientConfig::new()
        .set("bootstrap.servers", kafka_brokers)
        .set("group.id", app_id)
        .set("auto.offset.reset", auto_offset_reset)
        .set("enable.auto.commit", "true")
        .set("auto.commit.interval.ms", "5000")
        .create()
        .context("Failed to create Kafka consumer")?;

    consumer
        .subscribe(&[topic])
        .context("Failed to subscribe to topic")?;

    info!("Successfully connected to Kafka and subscribed to topic: {}", topic);

    // Load or create Delta table
    let delta_table = load_or_create_table(table_path).await?;
    let schema = delta_table.snapshot()?.arrow_schema()?.clone();

    info!("Delta table loaded/created successfully");
    info!("Schema: {:?}", schema);

    let mut buffer: Vec<Value> = Vec::new();
    let mut last_write = std::time::Instant::now();

    loop {
        match consumer.recv().await {
            Ok(message) => {
                if let Some(payload) = message.payload() {
                    match serde_json::from_slice::<Value>(payload) {
                        Ok(json_value) => {
                            // Signal: response_received - Track HTTP status (default 200 for successful parse)
                            scrapy_metrics.response_received(200).await.ok();

                            buffer.push(json_value);
                            metrics.incr("messages.received").ok();

                            // Check if we should write the batch
                            let should_write = buffer.len() >= max_messages_per_batch
                                || last_write.elapsed() >= Duration::from_secs(allowed_latency);

                            if should_write {
                                info!("Writing batch of {} messages to Delta Lake", buffer.len());

                                match write_batch(&delta_table, &schema, &buffer, &metrics, &mut scrapy_metrics).await {
                                    Ok(()) => {
                                        info!("Successfully wrote {} records", buffer.len());
                                        metrics.count("records.written", buffer.len() as i64).ok();
                                        buffer.clear();
                                        last_write = std::time::Instant::now();
                                    }
                                    Err(e) => {
                                        error!("Failed to write batch: {}", e);
                                        metrics.incr("errors.write_failed").ok();

                                        // Signal: spider_error
                                        scrapy_metrics.spider_error("write_failed", &e.to_string()).await.ok();
                                    }
                                }
                            }
                        }
                        Err(e) => {
                            warn!("Failed to parse message as JSON: {}", e);
                            metrics.incr("errors.parse_failed").ok();

                            // Signal: item_dropped
                            scrapy_metrics.item_dropped("parse_failed").await.ok();

                            // Signal: spider_error
                            scrapy_metrics.spider_error("parse_error", &e.to_string()).await.ok();
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

async fn load_or_create_table(table_path: &str) -> Result<DeltaTable> {
    // Try to load existing table
    match DeltaTableBuilder::from_uri(table_path).load().await {
        Ok(table) => {
            info!("Loaded existing Delta table from: {}", table_path);
            Ok(table)
        }
        Err(_) => {
            info!("Creating new Delta table at: {}", table_path);

            // Define schema for scraped items using Delta kernel types
            let fields = vec![
                StructField::new("url", DeltaDataType::STRING, false),
                StructField::new("title", DeltaDataType::STRING, true),
                StructField::new("content", DeltaDataType::STRING, true),
                StructField::new("scraped_at_utc", DeltaDataType::STRING, false),
                StructField::new("spider_name", DeltaDataType::STRING, false),
                StructField::new("pipeline_version", DeltaDataType::STRING, true),
            ];

            deltalake::operations::create::CreateBuilder::new()
                .with_location(table_path)
                .with_columns(fields)
                .await
                .context("Failed to create Delta table")
        }
    }
}

async fn write_batch(
    table: &DeltaTable,
    schema: &Schema,
    records: &[Value],
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

    for record in records {
        urls.push(record.get("url").and_then(|v| v.as_str()).unwrap_or(""));
        titles.push(record.get("title").and_then(|v| v.as_str()));
        contents.push(record.get("content").and_then(|v| v.as_str()));
        scraped_ats.push(record.get("scraped_at_utc").and_then(|v| v.as_str()).unwrap_or(""));
        spider_names.push(record.get("spider_name").and_then(|v| v.as_str()).unwrap_or(""));
        pipeline_versions.push(record.get("pipeline_version").and_then(|v| v.as_str()));

        // Signal: item_scraped for each successfully written item
        scrapy_metrics.item_scraped().await.ok();
    }

    let batch = RecordBatch::try_new(
        Arc::new(schema.clone()),
        vec![
            Arc::new(StringArray::from(urls)),
            Arc::new(StringArray::from(titles)),
            Arc::new(StringArray::from(contents)),
            Arc::new(StringArray::from(scraped_ats)),
            Arc::new(StringArray::from(spider_names)),
            Arc::new(StringArray::from(pipeline_versions)),
        ],
    )?;

    // Write to Delta Lake
    let mut writer = RecordBatchWriter::for_table(table)?;
    writer.write(batch).await?;
    let mut table_mut = table.clone();
    writer.flush_and_commit(&mut table_mut).await?;

    metrics.incr("batches.written").ok();

    Ok(())
}
