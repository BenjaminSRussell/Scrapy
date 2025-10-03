# Java Data Warehouse Loader Specification

## Overview

This document specifies a Java-based data warehouse ETL (Extract, Transform, Load) application that reads raw enriched data from the Python scraping pipeline and performs heavy-duty transformations before loading into a production PostgreSQL data warehouse.

**Architecture Pattern**: Python for data collection → Java for transformation & loading

---

## Why Java for Warehouse Loading?

### Advantages

1. **Enterprise Integration**: Better integration with enterprise data tools (Informatica, Talend, Apache NiFi)
2. **Performance**: JVM optimization for batch processing and large-scale transformations
3. **Type Safety**: Strong typing reduces errors in production ETL
4. **Ecosystem**: Rich libraries for database connectivity (JDBC, Spring Data, Hibernate)
5. **Scalability**: Built-in support for distributed processing (Spark, Flink)
6. **Monitoring**: Enterprise-grade monitoring with JMX, Prometheus exporters

### Division of Responsibilities

| Component | Technology | Responsibility |
|-----------|-----------|----------------|
| **Scraping Pipeline** | Python (Scrapy, spaCy) | Data collection, NLP extraction, initial enrichment |
| **Raw Data Storage** | Python | Write JSONL/Parquet files to staging area |
| **ETL Processor** | Java | Complex transformations, deduplication, data quality |
| **Data Warehouse** | PostgreSQL/Redshift | Final storage with optimized schema |

---

## System Architecture

```
┌─────────────────────────────────────────────────────────┐
│                 Python Scraping Pipeline                │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐    │
│  │   Stage 1   │→ │   Stage 2   │→ │   Stage 3   │    │
│  │  Discovery  │  │ Validation  │  │ Enrichment  │    │
│  └─────────────┘  └─────────────┘  └─────────────┘    │
│                         │                               │
│                         ↓                               │
│              ┌──────────────────────┐                  │
│              │  Raw Data Output     │                  │
│              │  (JSONL/Parquet)     │                  │
│              └──────────────────────┘                  │
└───────────────────────┬─────────────────────────────────┘
                        │
                        ↓
┌─────────────────────────────────────────────────────────┐
│              Java ETL Warehouse Loader                  │
│                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐ │
│  │   Extract    │→ │  Transform   │→ │     Load     │ │
│  │  (Read JSONL)│  │ (Normalize)  │  │(PostgreSQL)  │ │
│  └──────────────┘  └──────────────┘  └──────────────┘ │
│                                                         │
│  Features:                                              │
│  - Data validation & quality checks                     │
│  - Deduplication & conflict resolution                  │
│  - Schema migration & versioning                        │
│  - Incremental loading                                  │
│  - Error handling & retry logic                         │
│  - Performance monitoring                               │
└───────────────────────┬─────────────────────────────────┘
                        │
                        ↓
┌─────────────────────────────────────────────────────────┐
│            PostgreSQL Data Warehouse                    │
│                                                         │
│  Tables:                                                │
│  - pages (fact table)                                   │
│  - entities, keywords, categories (dimension tables)    │
│  - crawl_history, page_changes (audit tables)           │
│  - vendor_data (external integrations)                  │
└─────────────────────────────────────────────────────────┘
```

---

## Java Application Specification

### Technology Stack

**Core Framework**: Spring Boot 3.x
- Spring Data JPA for database access
- Spring Batch for ETL processing
- Spring Integration for file watching

**Database**:
- JDBC driver: PostgreSQL JDBC
- Connection pooling: HikariCP
- Migration: Flyway or Liquibase

**Data Processing**:
- JSON parsing: Jackson or Gson
- Parquet reading: Apache Parquet Java
- Data validation: Hibernate Validator

**Monitoring**:
- Metrics: Micrometer + Prometheus
- Logging: SLF4J + Logback
- Tracing: Spring Cloud Sleuth

### Project Structure

```
warehouse-loader/
├── src/
│   ├── main/
│   │   ├── java/
│   │   │   └── edu/uconn/warehouse/
│   │   │       ├── Application.java
│   │   │       ├── config/
│   │   │       │   ├── DatabaseConfig.java
│   │   │       │   ├── BatchConfig.java
│   │   │       │   └── MetricsConfig.java
│   │   │       ├── model/
│   │   │       │   ├── Page.java
│   │   │       │   ├── Entity.java
│   │   │       │   ├── Keyword.java
│   │   │       │   └── Category.java
│   │   │       ├── repository/
│   │   │       │   ├── PageRepository.java
│   │   │       │   ├── EntityRepository.java
│   │   │       │   └── ...
│   │   │       ├── service/
│   │   │       │   ├── DataLoader.java
│   │   │       │   ├── DataValidator.java
│   │   │       │   ├── DeduplicationService.java
│   │   │       │   └── TransformationService.java
│   │   │       ├── batch/
│   │   │       │   ├── JsonItemReader.java
│   │   │       │   ├── PageItemProcessor.java
│   │   │       │   └── PageItemWriter.java
│   │   │       └── util/
│   │   │           ├── HashUtils.java
│   │   │           └── DateUtils.java
│   │   └── resources/
│   │       ├── application.yml
│   │       ├── application-dev.yml
│   │       ├── application-prod.yml
│   │       └── db/migration/
│   │           ├── V1__create_schema.sql
│   │           └── V2__add_indexes.sql
│   └── test/
│       └── java/
│           └── edu/uconn/warehouse/
│               └── ... (unit and integration tests)
├── pom.xml (or build.gradle)
└── README.md
```

---

## Core Components

### 1. Data Model (JPA Entities)

**Page.java**
```java
@Entity
@Table(name = "pages", indexes = {
    @Index(name = "idx_url_hash", columnList = "url_hash"),
    @Index(name = "idx_last_crawled", columnList = "last_crawled_at")
})
public class Page {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long pageId;

    @Column(nullable = false)
    private String url;

    @Column(nullable = false, unique = true)
    private String urlHash;

    private String title;

    @Lob
    private String textContent;

    private Integer wordCount;
    private String contentType;
    private Integer statusCode;

    private Boolean hasPdfLinks;
    private Boolean hasAudioLinks;

    @Column(nullable = false)
    private LocalDateTime firstSeenAt;

    @Column(nullable = false)
    private LocalDateTime lastCrawledAt;

    private Integer crawlVersion;
    private Boolean isCurrent;

    @Column(nullable = false)
    private LocalDateTime createdAt;

    @Column(nullable = false)
    private LocalDateTime updatedAt;

    @OneToMany(mappedBy = "page", cascade = CascadeType.ALL, orphanRemoval = true)
    private List<Entity> entities = new ArrayList<>();

    @OneToMany(mappedBy = "page", cascade = CascadeType.ALL, orphanRemoval = true)
    private List<Keyword> keywords = new ArrayList<>();

    @OneToMany(mappedBy = "page", cascade = CascadeType.ALL, orphanRemoval = true)
    private List<Category> categories = new ArrayList<>();

    // Getters, setters, constructors
}
```

### 2. Spring Batch Configuration

**BatchConfig.java**
```java
@Configuration
@EnableBatchProcessing
public class BatchConfig {

    @Bean
    public Job importDataJob(JobRepository jobRepository, Step step1) {
        return new JobBuilder("importDataJob", jobRepository)
                .start(step1)
                .build();
    }

    @Bean
    public Step step1(JobRepository jobRepository,
                     PlatformTransactionManager transactionManager,
                     ItemReader<EnrichedData> reader,
                     ItemProcessor<EnrichedData, Page> processor,
                     ItemWriter<Page> writer) {
        return new StepBuilder("step1", jobRepository)
                .<EnrichedData, Page>chunk(100, transactionManager)
                .reader(reader)
                .processor(processor)
                .writer(writer)
                .build();
    }

    @Bean
    public ItemReader<EnrichedData> jsonReader() {
        return new JsonItemReaderBuilder<EnrichedData>()
                .jsonObjectReader(new JacksonJsonObjectReader<>(EnrichedData.class))
                .resource(new FileSystemResource("data/processed/stage03/enriched_content.jsonl"))
                .name("jsonReader")
                .build();
    }

    @Bean
    public ItemProcessor<EnrichedData, Page> processor() {
        return new PageItemProcessor();
    }

    @Bean
    public ItemWriter<Page> writer(PageRepository repository) {
        return new RepositoryItemWriterBuilder<Page>()
                .repository(repository)
                .methodName("save")
                .build();
    }
}
```

### 3. Data Processor

**PageItemProcessor.java**
```java
public class PageItemProcessor implements ItemProcessor<EnrichedData, Page> {

    @Autowired
    private DeduplicationService deduplicationService;

    @Autowired
    private DataValidator dataValidator;

    @Override
    public Page process(EnrichedData item) throws Exception {
        // Validate data quality
        if (!dataValidator.isValid(item)) {
            return null; // Skip invalid items
        }

        // Check for duplicates
        Optional<Page> existing = deduplicationService.findExisting(item.getUrlHash());

        Page page;
        if (existing.isPresent()) {
            page = existing.get();
            // Update version
            page.setCrawlVersion(page.getCrawlVersion() + 1);
            page.setIsCurrent(true);

            // Track changes
            if (!Objects.equals(page.getTextContent(), item.getTextContent())) {
                // Record content change
            }
        } else {
            page = new Page();
            page.setFirstSeenAt(LocalDateTime.now());
            page.setCrawlVersion(1);
            page.setIsCurrent(true);
        }

        // Map fields
        page.setUrl(item.getUrl());
        page.setUrlHash(item.getUrlHash());
        page.setTitle(item.getTitle());
        page.setTextContent(item.getTextContent());
        page.setWordCount(item.getWordCount());
        page.setContentType(item.getContentType());
        page.setStatusCode(item.getStatusCode());
        page.setLastCrawledAt(LocalDateTime.now());
        page.setUpdatedAt(LocalDateTime.now());

        // Map entities
        page.getEntities().clear();
        for (String entityText : item.getEntities()) {
            Entity entity = new Entity();
            entity.setEntityText(entityText);
            entity.setSource("nlp");
            entity.setPage(page);
            page.getEntities().add(entity);
        }

        // Map keywords
        page.getKeywords().clear();
        for (String keywordText : item.getKeywords()) {
            Keyword keyword = new Keyword();
            keyword.setKeywordText(keywordText);
            keyword.setSource("nlp");
            keyword.setPage(page);
            page.getKeywords().add(keyword);
        }

        // Map categories
        page.getCategories().clear();
        for (String categoryName : item.getContentTags()) {
            Category category = new Category();
            category.setCategoryName(categoryName);
            category.setPage(page);
            page.getCategories().add(category);
        }

        return page;
    }
}
```

### 4. Deduplication Service

**DeduplicationService.java**
```java
@Service
public class DeduplicationService {

    @Autowired
    private PageRepository pageRepository;

    public Optional<Page> findExisting(String urlHash) {
        return pageRepository.findByUrlHashAndIsCurrentTrue(urlHash);
    }

    public void markPreviousVersionsAsNotCurrent(String urlHash) {
        List<Page> previousVersions = pageRepository.findByUrlHashAndIsCurrentTrue(urlHash);
        previousVersions.forEach(page -> {
            page.setIsCurrent(false);
        });
        pageRepository.saveAll(previousVersions);
    }

    public boolean hasDuplicate(String urlHash) {
        return pageRepository.existsByUrlHash(urlHash);
    }
}
```

---

## Configuration Files

### application.yml

```yaml
spring:
  application:
    name: uconn-warehouse-loader

  datasource:
    url: jdbc:postgresql://localhost:5432/uconn_warehouse
    username: warehouse_user
    password: ${DB_PASSWORD}
    driver-class-name: org.postgresql.Driver
    hikari:
      maximum-pool-size: 20
      minimum-idle: 5
      connection-timeout: 30000

  jpa:
    hibernate:
      ddl-auto: validate  # Use Flyway for migrations
    show-sql: false
    properties:
      hibernate:
        dialect: org.hibernate.dialect.PostgreSQLDialect
        jdbc:
          batch_size: 50
        order_inserts: true
        order_updates: true

  batch:
    job:
      enabled: true
    jdbc:
      initialize-schema: always

  flyway:
    enabled: true
    locations: classpath:db/migration

warehouse:
  input:
    path: /data/processed/stage03/enriched_content.jsonl
    format: jsonl  # or parquet

  processing:
    chunk-size: 100
    skip-invalid: true
    validate-schema: true

  deduplication:
    enabled: true
    strategy: url-hash  # url-hash, content-hash, fuzzy

management:
  endpoints:
    web:
      exposure:
        include: health,metrics,info,prometheus
  metrics:
    export:
      prometheus:
        enabled: true

logging:
  level:
    root: INFO
    edu.uconn.warehouse: DEBUG
  file:
    name: logs/warehouse-loader.log
```

---

## Data Flow

### Extract Phase
1. **Read JSONL files** from Python pipeline output directory
2. **Parse JSON** into POJOs using Jackson
3. **Validate schema** against expected structure
4. **Log extraction metrics** (files read, records extracted)

### Transform Phase
1. **Data validation**
   - Check required fields
   - Validate data types
   - Enforce business rules

2. **Deduplication**
   - Check URL hash against existing records
   - Determine if update or insert
   - Calculate content diff for changes

3. **Normalization**
   - Split denormalized data into relational tables
   - Generate foreign keys
   - Apply data quality rules

4. **Enrichment**
   - Calculate derived fields
   - Apply business logic transformations
   - Add audit fields (created_at, updated_at)

### Load Phase
1. **Bulk insert** using Spring Batch chunk processing
2. **Transaction management** with rollback on errors
3. **Index updates** (defer during bulk load, rebuild after)
4. **Metrics recording** (records loaded, failures, duration)

---

## Error Handling Strategy

### Error Types

| Error Type | Handling Strategy | Action |
|------------|------------------|--------|
| **Schema Validation** | Skip record | Log error, move to error queue |
| **Duplicate Key** | Upsert | Update existing with new version |
| **Data Quality** | Skip or default | Log warning, use default value or skip |
| **Database Constraint** | Retry | Exponential backoff, max 3 retries |
| **Connection Failure** | Retry | Reconnect, resume from checkpoint |

### Error Table

```sql
CREATE TABLE load_errors (
    error_id SERIAL PRIMARY KEY,
    batch_id VARCHAR(50),
    record_data JSONB,
    error_type VARCHAR(50),
    error_message TEXT,
    stack_trace TEXT,
    occurred_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## Deployment

### Build

```bash
# Maven
mvn clean package

# Gradle
./gradlew build
```

### Run

```bash
# Development
java -jar warehouse-loader.jar --spring.profiles.active=dev

# Production
java -jar warehouse-loader.jar \
  --spring.profiles.active=prod \
  --spring.datasource.url=jdbc:postgresql://prod-db:5432/warehouse \
  --spring.datasource.password=${DB_PASSWORD}
```

### Docker

```dockerfile
FROM openjdk:17-jdk-slim
COPY target/warehouse-loader.jar /app/warehouse-loader.jar
WORKDIR /app
ENTRYPOINT ["java", "-jar", "warehouse-loader.jar"]
```

---

## Monitoring & Observability

### Metrics (Prometheus)

- `warehouse_records_processed_total{status="success|failure"}`
- `warehouse_batch_duration_seconds`
- `warehouse_database_connections_active`
- `warehouse_deduplication_hits_total`

### Logging

```java
logger.info("Starting batch job for file: {}", inputFile);
logger.debug("Processing record with URL hash: {}", urlHash);
logger.warn("Data quality issue for record: {}", record);
logger.error("Failed to load record", exception);
```

### Dashboards

- **Grafana dashboard** for real-time metrics
- **ELK stack** for log analysis
- **Airflow/Luigi** for job orchestration

---

## Testing Strategy

### Unit Tests
```java
@Test
public void testPageProcessor() {
    EnrichedData input = createTestData();
    Page result = processor.process(input);

    assertNotNull(result);
    assertEquals("Test Title", result.getTitle());
    assertEquals(3, result.getEntities().size());
}
```

### Integration Tests
```java
@SpringBootTest
@Testcontainers
public class LoaderIntegrationTest {

    @Container
    static PostgreSQLContainer<?> postgres = new PostgreSQLContainer<>("postgres:15");

    @Test
    public void testFullETLPipeline() {
        // Test end-to-end flow
    }
}
```

---

## Migration from Python to Java

### Phase 1: Parallel Run
- Python writes to SQLite
- Java reads from JSONL, writes to PostgreSQL
- Compare outputs for validation

### Phase 2: Gradual Cutover
- Switch read workloads to PostgreSQL
- Python continues to write JSONL
- Java is primary loader

### Phase 3: Full Java ETL
- Python outputs raw data only
- Java handles all transformations
- Retire Python warehouse code

---

## Performance Optimization

1. **Batch Processing**: Process 100-1000 records per transaction
2. **Connection Pooling**: HikariCP with 20 connections
3. **Index Strategy**: Disable indexes during load, rebuild after
4. **Partitioning**: Partition pages table by crawl_timestamp
5. **Async Processing**: Use CompletableFuture for independent operations

---

## Summary

This Java Data Warehouse Loader provides:

✅ **Enterprise-grade ETL** with Spring Batch
✅ **Relational normalization** for efficient querying
✅ **Version tracking** and change history
✅ **Vendor data integration** ready
✅ **PostgreSQL optimization** for scale
✅ **Comprehensive monitoring** and error handling

**Next Steps**:
1. Implement the Java loader using this specification
2. Set up CI/CD pipeline for automated builds
3. Create Grafana dashboards for monitoring
4. Write integration tests with Testcontainers
5. Deploy to production with blue-green deployment
