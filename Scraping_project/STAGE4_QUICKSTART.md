# Stage 4 Entity Summarization - Quick Start Guide

## What Is This?

The Stage 4 Entity Summarization Pipeline transforms your scraped content from **document-level summaries** into **entity-centric knowledge bases** with:

✅ **Verifiable Facts**: Every statement is linked to its source(s) with inline citations
✅ **No Redundancy**: Semantic deduplication removes repetitive information
✅ **Chronological Awareness**: Recent information is automatically prioritized
✅ **Self-Contained**: Uses only local Hugging Face models (no external APIs)

## Quick Start (5 Minutes)

### 1. Install Dependencies

```bash
pip install -r requirements-stage4.txt
```

This installs:
- `sentence-transformers` (for semantic deduplication)
- `transformers` (for BART summarization)
- `torch` (PyTorch backend)

### 2. Run the Demo

```bash
python examples/entity_summarization_demo.py
```

This interactive demo shows:
1. **Phase 1**: Extracting and deduplicating facts from 4 sample documents
2. **Phase 2**: Sorting facts chronologically and generating an abstractive summary
3. **Phase 3**: Storing the result in Delta Lake with citations

**Expected Output**:
```
PHASE 1: EXTRACTIVE FACT AGGREGATION AND DEDUPLICATION
Result: 12 raw facts → 6 unique facts after deduplication

Deduplicated Facts:
1. [2020-08-15] Professor Jane Doe joined the University of Connecticut in 2020 as an Associate Professor of Computer Science.
   Sources: 2 document(s)

2. [2021-03-10] Dr. Jane Doe, who joined UConn in 2020, has been awarded the prestigious NSF CAREER Award...
   Sources: 2 document(s)

...

PHASE 2B: ABSTRACTIVE SUMMARIZATION WITH CITATIONS
Generated Summary:
Professor Jane Doe joined UConn in 2020 and received the NSF CAREER Award in 2021. [1] [2]
She published a groundbreaking paper in Nature in 2023. [3]
```

### 3. Integrate with Your Pipeline

#### Option A: Batch Processing from Delta Lake

```python
from src.stage4.entity_worker_example import EntityWorkerRunner

runner = EntityWorkerRunner(
    input_table="stage3_analytics",  # Your Stage 3 output
    batch_size=100
)

runner.run(limit=1000)  # Process first 1000 records
```

**Command Line**:
```bash
python -m src.stage4.entity_worker_example \
    --mode delta \
    --input-table stage3_analytics \
    --limit 1000
```

#### Option B: Real-Time Streaming from Kafka

```bash
python -m src.stage4.entity_worker_example \
    --mode kafka \
    --kafka-topic final_categorized
```

This continuously consumes from Kafka and generates summaries in real-time.

## How It Works

### The Pipeline

```
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 1: Extractive Fact Aggregation & Deduplication           │
├─────────────────────────────────────────────────────────────────┤
│ Input:  Multiple documents about "Professor Jane Doe"          │
│         • Doc 1: "Jane Doe joined UConn in 2020..."           │
│         • Doc 2: "Dr. Jane Doe joined UConn in 2020..."       │  (duplicate!)
│         • Doc 3: "Professor Doe received NSF Award..."         │
│                                                                 │
│ Process: 1. Extract sentences mentioning the entity            │
│          2. Generate sentence embeddings (all-MiniLM-L6-v2)   │
│          3. Cluster similar sentences (cosine similarity)      │
│          4. Select representative from each cluster            │
│                                                                 │
│ Output:  6 unique facts with source references                 │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 2: Chronological Sorting & Abstractive Summarization     │
├─────────────────────────────────────────────────────────────────┤
│ Input:  Deduplicated facts                                      │
│                                                                 │
│ Process: 1. Sort facts by publication date (oldest → newest)   │
│          2. Prepend dates: "(2020-08-15): Jane Doe joined..." │
│          3. Feed to BART model for abstractive summary         │
│          4. Embed inline citations [1], [2], [3]              │
│                                                                 │
│ Output:  "Professor Jane Doe joined UConn in 2020 and          │
│          received the NSF CAREER Award in 2021. [1] [2]"       │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ PHASE 3: Structured Storage in Delta Lake                      │
├─────────────────────────────────────────────────────────────────┤
│ Table: entity_summaries                                         │
│ Schema:                                                         │
│   • entity_name: "Professor Jane Doe"                          │
│   • entity_type: "person"                                      │
│   • summary_text: "Professor Jane Doe joined UConn..."         │
│   • source_references: {1: [{url: "...", date: "..."}], ...} │
│   • last_updated: "2024-06-01T00:00:00"                        │
│   • fact_count: 6                                              │
└─────────────────────────────────────────────────────────────────┘
```

## Key Components

### 1. FactAggregator
**Purpose**: Extract and deduplicate facts by entity

```python
from src.stage4.entity_summarization import FactAggregator

aggregator = FactAggregator(
    similarity_threshold=0.85  # Adjust for more/less deduplication
)

aggregator.add_document(
    entity_name="Professor Jane Doe",
    entity_type="person",
    content="Professor Jane Doe joined UConn in 2020...",
    source_url="https://uconn.edu/faculty/jane-doe",
    publication_date="2020-08-15"
)

# Get deduplicated facts
facts = aggregator.deduplicate_facts("Professor Jane Doe")
```

### 2. AbstractiveSummarizer
**Purpose**: Generate coherent summaries with citations

```python
from src.stage4.entity_summarization import AbstractiveSummarizer

summarizer = AbstractiveSummarizer(
    model_name="facebook/bart-large-cnn",
    device=-1  # CPU
)

result = summarizer.summarize(formatted_text, facts)
# → {"summary_text": "...", "citations": {1: [...], 2: [...]}}
```

### 3. Stage4EntityWorker
**Purpose**: Orchestrate the complete pipeline

```python
from src.stage4.entity_summarization import Stage4EntityWorker

worker = Stage4EntityWorker()

documents = [
    {
        "entity_name": "Professor Jane Doe",
        "entity_type": "person",
        "content": "...",
        "source_url": "https://...",
        "publication_date": datetime(2020, 1, 1),
    },
    # More documents...
]

worker.process_documents(documents)
# → Summaries automatically saved to Delta Lake
```

## Configuration

Create `config/entity_summarization.yml` (or add to your existing config):

```yaml
entity_summarization:
  # Models
  embedding_model: "sentence-transformers/all-MiniLM-L6-v2"
  summarization_model: "facebook/bart-large-cnn"

  # Deduplication
  similarity_threshold: 0.85  # 0.80-0.90 recommended

  # Processing
  batch_size: 100
  device: -1  # -1 for CPU, 0 for GPU

  # Input/Output
  delta_input_table: "stage3_analytics"
  delta_table_name: "entity_summaries"
```

## Query Results

### From Python

```python
from src.common.delta_lake import get_delta_manager

delta = get_delta_manager()

# Get all summaries
summaries = delta.read("entity_summaries")

# Get summaries for specific entity
jane_summaries = delta.read(
    "entity_summaries",
    filters="entity_name = 'Professor Jane Doe'"
)

# Print summary
for summary in summaries:
    print(f"Entity: {summary['entity_name']}")
    print(f"Summary: {summary['summary_text']}")
    print(f"Sources: {summary['fact_count']} facts")
    print()
```

### From SQL (DuckDB/Spark)

```sql
SELECT
    entity_name,
    entity_type,
    summary_text,
    fact_count,
    last_updated
FROM delta.`/path/to/data/delta_lake/entity_summaries`
WHERE entity_type = 'person'
ORDER BY last_updated DESC;
```

## Performance Tips

### GPU Acceleration (10x faster)

```python
worker = Stage4EntityWorker(device=0)  # Use first GPU
```

**Requirements**: CUDA-enabled GPU + `torch` with CUDA support

### Batch Processing

Process documents in batches to amortize model loading:

```python
# Good: Process 100 documents at once
worker.process_documents(documents[:100])

# Bad: Process one at a time
for doc in documents:
    worker.process_documents([doc])  # Inefficient!
```

### Model Selection

**Fast (for development)**:
```python
embedding_model="sentence-transformers/all-MiniLM-L6-v2"  # 90MB
summarization_model="facebook/bart-base"  # 560MB
```

**Best Quality (for production)**:
```python
embedding_model="sentence-transformers/all-mpnet-base-v2"  # 420MB
summarization_model="facebook/bart-large-cnn"  # 1.6GB
```

## Testing

Run the test suite:

```bash
# Unit tests (fast, with mocked models)
pytest tests/unit/stage4/test_entity_summarization.py -v

# Integration tests (slow, with real models)
pytest tests/unit/stage4/test_entity_summarization.py -m integration -v
```

## Troubleshooting

### "OutOfMemoryError" on GPU

**Solution**: Use CPU or reduce batch size
```python
worker = Stage4EntityWorker(device=-1, batch_size=50)
```

### Slow Processing

**Solution**: Enable GPU or use smaller models
```python
worker = Stage4EntityWorker(
    device=0,  # GPU
    summarization_model="facebook/bart-base"  # Smaller model
)
```

### Too Much Deduplication

**Solution**: Lower similarity threshold
```python
aggregator = FactAggregator(similarity_threshold=0.80)  # Was 0.85
```

### Too Many Duplicate Facts

**Solution**: Raise similarity threshold
```python
aggregator = FactAggregator(similarity_threshold=0.90)  # Was 0.85
```

## Next Steps

1. **Read the full documentation**: [docs/ENTITY_SUMMARIZATION.md](docs/ENTITY_SUMMARIZATION.md)
2. **Customize entity extraction**: Implement Named Entity Recognition (NER)
3. **Tune parameters**: Adjust `similarity_threshold`, `batch_size`, etc.
4. **Deploy to production**: Use Kafka streaming mode for real-time processing

## Architecture Overview

```
┌──────────────────┐
│  Stage 1 & 2     │  Scraping & Classification
│  (Existing)      │
└────────┬─────────┘
         │
         ↓
┌──────────────────┐
│  Stage 3         │  Document Summarization
│  (Existing)      │
└────────┬─────────┘
         │
         ↓
┌──────────────────┐
│  Delta Lake      │  stage3_analytics
│  (or Kafka)      │  final_categorized
└────────┬─────────┘
         │
         ↓
┌──────────────────────────────────────────────┐
│  Stage 4: Entity Summarization (NEW!)        │
│                                              │
│  ┌──────────────────────────────────────┐  │
│  │ Phase 1: FactAggregator              │  │
│  │ • Extract sentences per entity       │  │
│  │ • Semantic deduplication             │  │
│  └──────────────────────────────────────┘  │
│                 ↓                            │
│  ┌──────────────────────────────────────┐  │
│  │ Phase 2: ChronologicalSorter         │  │
│  │          + AbstractiveSummarizer     │  │
│  │ • Sort by date                       │  │
│  │ • Generate BART summary              │  │
│  │ • Embed citations                    │  │
│  └──────────────────────────────────────┘  │
│                 ↓                            │
│  ┌──────────────────────────────────────┐  │
│  │ Phase 3: EntitySummaryStorage        │  │
│  │ • Write to Delta Lake                │  │
│  └──────────────────────────────────────┘  │
└──────────────────────────────────────────────┘
         │
         ↓
┌──────────────────┐
│  Delta Lake      │  entity_summaries
│                  │  (Queryable with SQL!)
└──────────────────┘
```

## Support

- **Documentation**: [docs/ENTITY_SUMMARIZATION.md](docs/ENTITY_SUMMARIZATION.md)
- **Example Code**: [examples/entity_summarization_demo.py](examples/entity_summarization_demo.py)
- **Tests**: [tests/unit/stage4/test_entity_summarization.py](tests/unit/stage4/test_entity_summarization.py)

---

**Ready to get started?**

```bash
# Install dependencies
pip install -r requirements-stage4.txt

# Run the demo
python examples/entity_summarization_demo.py

# Start processing your data
python -m src.stage4.entity_worker_example --mode delta --limit 100
```

Happy summarizing! 🚀
