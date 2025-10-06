use anyhow::{Context, Result};
use arrow::array::{RecordBatch, StringArray};
use arrow::datatypes::{DataType, Field, Schema};
use cadence::{StatsdClient, UdpMetricSink};
use clap::{Parser, Subcommand};
use deltalake::arrow::array::StructArray;
use deltalake::writer::{DeltaWriter, RecordBatchWriter};
use deltalake::{DeltaTable, DeltaTableBuilder};
use rdkafka::config::ClientConfig;
use rdkafka::consumer::{Consumer, StreamConsumer};
use rdkafka::Message;
use serde_json::Value;
use std::collections::HashMap;
use std::net::UdpSocket;
use std::sync::Arc;
use std::time::Duration;
use tracing::{error, info, warn};

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

        /// Delta Lake table path (e.g., s3://bucket/path or /local/path)
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
    let schema = delta_table.get_schema()?.clone();

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
                            buffer.push(json_value);
                            metrics.incr("messages.received").ok();

                            // Check if we should write the batch
                            let should_write = buffer.len() >= max_messages_per_batch
                                || last_write.elapsed() >= Duration::from_secs(allowed_latency);

                            if should_write {
                                info!("Writing batch of {} messages to Delta Lake", buffer.len());

                                match write_batch(&delta_table, &schema, &buffer, &metrics).await {
                                    Ok(()) => {
                                        info!("Successfully wrote {} records", buffer.len());
                                        metrics.count("records.written", buffer.len() as i64).ok();
                                        buffer.clear();
                                        last_write = std::time::Instant::now();
                                    }
                                    Err(e) => {
                                        error!("Failed to write batch: {}", e);
                                        metrics.incr("errors.write_failed").ok();
                                    }
                                }
                            }
                        }
                        Err(e) => {
                            warn!("Failed to parse message as JSON: {}", e);
                            metrics.incr("errors.parse_failed").ok();
                        }
                    }
                }
            }
            Err(e) => {
                warn!("Kafka error: {}", e);
                metrics.incr("errors.kafka").ok();
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

            // Define schema for scraped items
            let schema = Arc::new(Schema::new(vec![
                Field::new("url", DataType::Utf8, false),
                Field::new("title", DataType::Utf8, true),
                Field::new("content", DataType::Utf8, true),
                Field::new("scraped_at_utc", DataType::Utf8, false),
                Field::new("spider_name", DataType::Utf8, false),
                Field::new("pipeline_version", DataType::Utf8, true),
            ]));

            DeltaTableBuilder::from_uri(table_path)
                .with_columns(schema.fields().clone())
                .build()
                .context("Failed to create Delta table")?
                .create()
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
    writer.flush_and_commit(table).await?;

    metrics.incr("batches.written").ok();

    Ok(())
}
