# UConn Web Scraping Pipeline - Complete Feature List

## 🎯 Pipeline Overview

Three-stage pipeline with Delta Lake storage, OCR, Whisper transcription, and NLP enrichment.

---

## 📍 STAGE 1: URL Discovery (Massive Collection)

### Purpose
Discover and collect MASSIVE amounts of URLs as quickly as possible.

### Key Features
1. **High-Performance Async Crawling**
   - Async/await with aiohttp for maximum speed
   - Configurable concurrency (default: 64-128 concurrent requests)
   - BFS (breadth-first search) crawling
   - Adaptive depth limits (per-section learning)

2. **Efficient Deduplication**
   - Bloom filter for fast initial checks (O(1) lookup)
   - Hash set for precise duplicate detection
   - URL canonicalization and normalization
   - Pre-discovery caching

3. **URL Extraction Methods**
   - HTML link extraction (<a> tags)
   - Sitemap parsing (XML sitemaps)
   - JavaScript URL detection (regex patterns)
   - Form action URLs
   - Meta refresh URLs
   - Canonical URLs

4. **Smart Filtering**
   - Domain whitelisting
   - URL pattern matching
   - Content-type validation
   - Binary file filtering
   - robots.txt respect (optional)

5. **Performance Optimizations**
   - Connection pooling
   - DNS caching (300s TTL)
   - HTTP/2 support
   - Gzip compression
   - Keep-alive connections

6. **Output**
   - **Primary**: Delta Lake table (`data/datalake/raw_urls/`)
   - Partitioned by: `crawl_date`
   - Schema:
     ```
     url: string
     url_hash: string
     discovered_from: string
     depth: int
     crawl_date: date
     discovery_method: string
     ```

### Configuration
- `max_depth`: How deep to crawl (default: 5 for massive collection)
- `max_urls`: Maximum URLs to discover (default: 50,000)
- `concurrency`: Concurrent requests (default: 128)
- `allowed_domains`: List of domains to scrape

### Performance Metrics
- **Expected throughput**: 100-500 URLs/sec
- **Memory usage**: ~500MB for 50K URLs
- **Time estimate**: 10-50K URLs in 2-5 minutes

---

## 📂 STAGE 2: File Validation & Processing

### Purpose
Validate URLs, detect file types, run OCR on images/PDFs, transcribe audio, and collect metadata.

### Key Features

#### 1. **File Type Detection**
Automatically identifies:
- **HTML/Web Pages**
- **PDFs** (.pdf)
- **Images**: .jpg, .jpeg, .png, .gif, .bmp, .tiff, .webp
- **Audio**: .mp3, .wav, .m4a, .ogg, .flac, .aac
- **Video**: .mp4, .avi, .mov, .wmv, .flv, .webm, .mkv
- **Documents**: .doc, .docx, .txt, .rtf

Detection methods:
- URL extension analysis
- Content-Type header inspection
- Magic byte detection (file signatures)
- MIME type validation

#### 2. **URL Validation**
- HEAD request for metadata (fast)
- GET fallback if HEAD fails
- Redirect chain following
- SSL/TLS verification
- HTTP status code checking
- Response size limits (prevent memory exhaustion)

#### 3. **PDF Processing**
- **Text extraction**: PyPDF2 for text-based PDFs
- **OCR for scanned PDFs**: EasyOCR when text extraction fails
- **Summarization**: Long PDFs summarized to paragraphs
- **Metadata**: Page count, author, creation date

#### 4. **Image Processing (OCR)**
- **Engine**: EasyOCR (better accuracy than Tesseract)
- **Languages**: English (configurable)
- **Supported formats**: All major image formats
- **Image preprocessing**: Auto-resize, contrast enhancement
- **Output**: Extracted text + confidence scores

#### 5. **Audio Transcription (Whisper)**
- **Model**: OpenAI Whisper (base model, configurable)
- **Languages**: Auto-detect or specify
- **Formats**: All major audio formats (via ffmpeg)
- **Features**:
  - Speaker diarization (optional)
  - Timestamp alignment
  - Automatic punctuation
  - Noise reduction
- **Summarization**: Long transcripts summarized

#### 6. **Video Processing**
- **Metadata extraction**: Duration, resolution, codec
- **Thumbnail extraction**: First frame
- **Audio extraction** → Whisper transcription
- **Future**: Frame sampling for OCR

#### 7. **Metadata Collection**
For ALL file types:
- Content-Type
- Content-Length
- Last-Modified date
- ETag
- Server header
- Response time
- Redirect chain
- Final URL (after redirects)

#### 8. **Content Categorization**
Automatic labels:
- `is_binary`: PDF, images, audio, video
- `is_media`: Audio, video, images
- `is_document`: PDF, DOC, TXT
- `requires_enrichment`: Needs Stage 3 NLP?
- `processing_method`: ocr, whisper, pdf_extraction, html

#### 9. **Quality Checks**
- Content-length validation
- MIME type verification
- Malformed URL detection
- Duplicate content detection (hash-based)
- Low-quality page filtering

### Output
- **Primary**: Delta Lake table (`data/datalake/validated_urls/`)
- Partitioned by: `validation_date`, `file_type`
- Schema:
  ```
  url: string
  url_hash: string
  is_valid: boolean
  status_code: int
  content_type: string
  content_length: long
  last_modified: string
  file_type: string
  file_extension: string
  extracted_text: string  # From OCR/Whisper
  text_preview: string  # First 500 chars
  processing_method: string
  requires_enrichment: boolean
  domain: string
  is_binary: boolean
  is_media: boolean
  is_document: boolean
  validation_date: date
  validation_timestamp: timestamp
  ```

### Performance Metrics
- **Expected throughput**: 50-200 URLs/sec (without OCR/Whisper)
- **With OCR**: 5-20 files/sec
- **With Whisper**: 1-5 files/sec (depending on audio length)
- **Concurrency**: 50-64 concurrent requests

---

## 🧠 STAGE 3: NLP Enrichment

### Purpose
Extract entities, keywords, topics, and semantic meaning from text content.

### Key Features

#### 1. **Entity Extraction (NER)**
- **Model**: DeBERTa-v3 (microsoft/deberta-v3-base)
- **Entities detected**:
  - PERSON (names)
  - ORGANIZATION (companies, universities)
  - LOCATION (cities, states)
  - DATE/TIME
  - MONEY/PERCENT
  - PRODUCT
  - EVENT
- **Custom entities**: Domain-specific (e.g., "Department", "Program")

#### 2. **Keyword Extraction**
- **Methods**:
  - TF-IDF (statistical)
  - YAKE (unsupervised)
  - spaCy noun phrases
  - Custom pattern matching
- **Glossary matching**: UConn-specific terms
- **Confidence scores**: For each keyword

#### 3. **Topic Classification**
- **Zero-shot classification**: DeBERTa
- **Taxonomy matching**: Predefined categories
- **Custom topics**: Academic departments, services, admissions, etc.

#### 4. **Text Summarization**
- **Model**: BART-large-CNN (facebook/bart-large-cnn)
- **Use cases**:
  - Long web pages → 1-2 paragraph summary
  - Video transcripts → Summary
  - PDF content → Executive summary
- **Configurable length**: min/max tokens

#### 5. **Sentiment Analysis**
- Positive/Negative/Neutral classification
- Confidence scores
- Aspect-based sentiment (optional)

#### 6. **Content Quality Scoring**
- Word count
- Sentence complexity
- Readability metrics
- Information density
- Duplicate content detection

#### 7. **Link Analysis**
- Internal vs external links
- Anchor text extraction
- Link context
- Outbound link quality

### Output
- **Primary**: Delta Lake table (`data/datalake/enriched_content/`)
- Partitioned by: `enrichment_date`
- Schema:
  ```
  url: string
  url_hash: string
  title: string
  text_content: string
  word_count: int
  entities: array<struct<text, type, confidence>>
  keywords: array<struct<keyword, score>>
  topics: array<string>
  summary: string
  sentiment: struct<score, label>
  content_tags: array<string>
  has_pdf_links: boolean
  has_audio_links: boolean
  has_video: boolean
  quality_score: double
  enrichment_date: date
  enrichment_timestamp: timestamp
  ```

---

## 🗄️ Delta Lake Storage

### Why Delta Lake?
- **ACID transactions**: Atomic writes, no corruption
- **Time travel**: Query historical data
- **Schema evolution**: Add fields without breaking
- **Fast queries**: Columnar Parquet format
- **Partitioning**: Lightning-fast filtered queries

### Tables

1. **raw_urls**
   - Location: `data/datalake/raw_urls/`
   - Partitions: crawl_date
   - Purpose: All discovered URLs

2. **validated_urls**
   - Location: `data/datalake/validated_urls/`
   - Partitions: validation_date, file_type
   - Purpose: Valid URLs with metadata + OCR/Whisper output

3. **enriched_content**
   - Location: `data/datalake/enriched_content/`
   - Partitions: enrichment_date
   - Purpose: NLP-enriched content

4. **link_graph**
   - Location: `data/datalake/link_graph/`
   - Purpose: PageRank, authority scores, link relationships

5. **performance_metrics**
   - Location: `data/datalake/performance_metrics/`
   - Purpose: Pipeline metrics and monitoring

### Query Examples

```python
# Get all PDFs with OCR text
from src.common.delta_lake import DeltaLakeReader
reader = DeltaLakeReader(DELTA_VALIDATED_URLS)
pdfs = reader.read(filters=[
    ('file_type', '=', 'pdf'),
    ('extracted_text', 'is not', None)
])

# Time travel: URLs discovered last week
reader = DeltaLakeReader(DELTA_RAW_URLS)
last_week = reader.read(timestamp='2025-09-27T00:00:00')

# SQL query
results = reader.query("title LIKE '%admissions%'")
```

---

## 🚀 Performance & Scalability

### Throughput
- **Stage 1**: 100-500 URLs/sec discovery
- **Stage 2**: 50-200 URLs/sec validation (no OCR)
- **Stage 2 OCR**: 5-20 files/sec
- **Stage 2 Whisper**: 1-5 files/sec
- **Stage 3**: 20-50 pages/sec enrichment

### Resource Usage
- **CPU**: 8+ cores recommended
- **RAM**: 16GB minimum, 64GB ideal
- **Disk**: 100GB+ for 1M URLs
- **GPU**: Optional (2-3x faster NLP)

### Scalability
- **Horizontal**: Multiple workers with shared Delta Lake
- **Vertical**: Increase concurrency and batch sizes
- **Cloud-ready**: S3-compatible Delta Lake storage

---

## 🛠️ Configuration

### Main Config (`config/development.yml`)
```yaml
pipeline:
  max_urls: 50000
  max_depth: 5
  concurrency: 128

stage1:
  seed_urls:
    - https://uconn.edu
  allowed_domains:
    - uconn.edu

stage2:
  concurrency: 64
  timeout: 30
  enable_ocr: true
  enable_whisper: true

stage3:
  concurrency: 50
  summarization: true
  sentiment_analysis: false
```

---

## 📊 Monitoring & Logging

### Structured Logging
- Event-based logging with JSON output
- Trace correlation (session_id, trace_id)
- Per-stage logging
- Error classification

### Metrics
- URLs processed per second
- Success/failure rates
- Processing times (p50, p95, p99)
- Resource usage (CPU, memory, disk)
- Queue depths

### Dashboards (Future)
- Real-time pipeline progress
- Error rates by stage
- Content type distribution
- NLP quality scores

---

## 🔄 Pipeline Execution

### Full Pipeline
```bash
python start.py --full
```

### Individual Stages
```bash
python start.py --stage1  # Discover URLs
python start.py --stage2  # Validate & process files
python start.py --stage3  # NLP enrichment
```

### Query Data
```bash
python start.py --query "title LIKE '%research%'"
python start.py --status  # Show stats
```

---

## 📝 Summary of Improvements

### What We Fixed
1. ✅ Consolidated data paths to Delta Lake (single source of truth)
2. ✅ Added OCR support (EasyOCR for PDFs and images)
3. ✅ Added Whisper transcription (audio files)
4. ✅ Added summarization model (BART-large-CNN)
5. ✅ Stage 2 now handles file detection, metadata, OCR, Whisper
6. ✅ Stage 3 focuses purely on NLP enrichment
7. ✅ Created unified `start.py` entry point
8. ✅ Added Delta Lake query interface
9. ✅ Removed legacy JSONL/SQLite references (still supported for compatibility)
10. ✅ Increased concurrency limits (16→64→128)

### Outstanding Issues to Address
- [ ] Fix Delta Lake schema compatibility (add missing fields)
- [ ] Test with real UConn data
- [ ] Add video frame extraction + OCR
- [ ] Implement adaptive concurrency based on server response
- [ ] Add circuit breakers for failing domains
- [ ] Implement proper retry strategies
- [ ] Add comprehensive error classification
- [ ] Create monitoring dashboard
