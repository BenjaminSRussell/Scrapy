# Entity-Centric Summarization Pipeline (Stage 4)

## Overview

The Stage 4 Entity Summarization Pipeline transforms document-level summaries into sophisticated, entity-centric summaries that are:

- **Verifiable**: Every fact is linked to its source(s) with inline citations
- **Non-redundant**: Semantic deduplication eliminates repetitive information
- **Chronologically-aware**: Recent information is prioritized in summaries
- **Self-contained**: Uses only local Hugging Face models (no external API dependencies)

## Architecture

The pipeline consists of three main phases:

### Phase 1: Extractive Fact Aggregation and Deduplication

**Class**: `FactAggregator`

**Purpose**: Extract and deduplicate facts at the entity level

**Process**:
1. **Fact Extraction**: Extracts sentences from documents where a specific entity is mentioned
2. **Source Attribution**: Links each fact to its source URL and publication date
3. **Semantic Deduplication**: Uses sentence embeddings (`all-MiniLM-L6-v2`) to identify semantically similar facts
4. **Clustering**: Groups similar facts using cosine similarity thresholds
5. **Representative Selection**: Selects the most representative fact from each cluster (prioritizing recency and detail)

**Output**: A clean, non-redundant list of facts for each entity, with aggregated source references

### Phase 2: Abstractive Summarization with Chronological Context

**Classes**: `ChronologicalSorter`, `AbstractiveSummarizer`

**Purpose**: Generate coherent narrative summaries that emphasize recent developments

**Process**:
1. **Chronological Sorting**: Orders facts by publication date (oldest to newest)
2. **Date Cueing**: Prepends each fact with its publication date to guide the model
   - Example: `(2023-10-16): Professor Doe received the Excellence Award.`
3. **Abstractive Summarization**: Uses BART (`facebook/bart-large-cnn`) to synthesize facts into a cohesive paragraph
4. **Citation Embedding**: Adds inline citations linking back to source references
   - Example: `"Professor Jane Doe received the ABC Award in 2024 [1]"`

**Output**: A narrative summary with embedded citations

### Phase 3: Structured Output and Storage

**Class**: `EntitySummaryStorage`

**Purpose**: Store summaries in Delta Lake with structured schema

**Delta Lake Schema**:
```python
{
    "entity_name": "string",           # Name of the entity
    "entity_type": "string",           # Type (person, organization, etc.)
    "summary_text": "string",          # Generated summary with citations
    "source_references": "string",     # JSON-encoded citation mapping
    "last_updated": "string",          # ISO 8601 timestamp of most recent source
    "fact_count": "int",               # Number of unique facts
    "created_at": "string"             # Pipeline execution timestamp
}
```

**Output**: Structured, queryable summaries in Delta Lake table `entity_summaries`

## Usage

### Basic Usage

```python
from src.stage4.entity_summarization import Stage4EntityWorker
from datetime import datetime

# Initialize worker
worker = Stage4EntityWorker(
    embedding_model="sentence-transformers/all-MiniLM-L6-v2",
    summarization_model="facebook/bart-large-cnn",
    similarity_threshold=0.85,  # Cosine similarity threshold for deduplication
    device=-1  # -1 for CPU, 0+ for GPU
)

# Prepare documents
documents = [
    {
        "entity_name": "Professor Jane Doe",
        "entity_type": "person",
        "content": "Professor Jane Doe joined UConn in 2020...",
        "source_url": "https://uconn.edu/faculty/jane-doe",
        "publication_date": datetime(2020, 1, 15),
        "metadata": {"department": "Computer Science"}
    },
    # More documents...
]

# Process documents
worker.process_documents(documents)
```

### Integration with Existing Pipeline

#### Option 1: Batch Processing from Delta Lake

```python
from src.stage4.entity_worker_example import EntityWorkerRunner

# Initialize runner
runner = EntityWorkerRunner(
    input_table="stage3_analytics",  # Read from Stage 3 output
    batch_size=100
)

# Run batch processing
runner.run(limit=1000)  # Process first 1000 records
```

**Command Line**:
```bash
python -m src.stage4.entity_worker_example --mode delta --input-table stage3_analytics --limit 1000
```

#### Option 2: Streaming from Kafka

```python
from src.stage4.entity_worker_example import KafkaEntityWorker

# Initialize Kafka consumer
worker = KafkaEntityWorker(
    kafka_topic="final_categorized",
    consumer_group="entity-worker-group",
    bootstrap_servers="localhost:9092"
)

# Start consuming and processing
worker.start_consuming()
```

**Command Line**:
```bash
python -m src.stage4.entity_worker_example --mode kafka --kafka-topic final_categorized
```

## Configuration

### Settings in `config.yml`

Add the following configuration to your YAML config file:

```yaml
# Stage 4: Entity Summarization Configuration
entity_summarization:
  # Embedding model for semantic deduplication
  embedding_model: "sentence-transformers/all-MiniLM-L6-v2"

  # Summarization model for abstractive summaries
  summarization_model: "facebook/bart-large-cnn"

  # Similarity threshold for deduplication (0.0 - 1.0)
  # Higher = more aggressive deduplication
  similarity_threshold: 0.85

  # Fact extraction parameters
  min_fact_length: 20      # Minimum characters for a valid fact
  max_fact_length: 500     # Maximum characters for a fact

  # Summarization parameters
  summary_max_length: 300  # Max tokens in summary
  summary_min_length: 100  # Min tokens in summary

  # Processing parameters
  batch_size: 100          # Documents per batch
  device: -1               # -1 for CPU, 0+ for GPU
```

## Delta Lake Table Schema

The `entity_summaries` table is automatically created with the following schema:

| Field | Type | Description |
|-------|------|-------------|
| `entity_name` | string | Name of the entity (e.g., "Professor Jane Doe") |
| `entity_type` | string | Entity type (person, organization, initiative, etc.) |
| `summary_text` | string | Generated abstractive summary with inline citations |
| `source_references` | string | JSON object mapping citation numbers to source URLs and dates |
| `last_updated` | string | ISO 8601 timestamp of most recent source document |
| `fact_count` | int | Number of unique facts aggregated |
| `created_at` | string | Timestamp when summary was generated |

### Example Record

```json
{
  "entity_name": "Professor Jane Doe",
  "entity_type": "person",
  "summary_text": "Professor Jane Doe joined UConn in 2020 and received the NSF CAREER Award in 2021. [1] She published a groundbreaking paper in Nature in 2023. [2]",
  "source_references": "{\"1\": [{\"url\": \"https://uconn.edu/faculty/jane-doe\", \"date\": \"2021-03-15\"}], \"2\": [{\"url\": \"https://uconn.edu/news/jane-doe-nature\", \"date\": \"2023-06-01\"}]}",
  "last_updated": "2023-06-01T00:00:00",
  "fact_count": 5,
  "created_at": "2025-10-16T10:30:00Z"
}
```

## Dependencies

Install the required packages:

```bash
pip install sentence-transformers transformers torch
```

**Minimal versions**:
- `sentence-transformers>=2.2.0`: For sentence embeddings
- `transformers>=4.30.0`: For BART summarization
- `torch>=2.0.0`: PyTorch backend

## Performance Considerations

### Model Loading

Models are **lazy-loaded** on first use to minimize startup time and memory footprint:
- Embedding model: ~90MB (all-MiniLM-L6-v2)
- Summarization model: ~1.6GB (bart-large-cnn)

**Recommendation**: For production, use GPU inference to speed up processing.

### Batch Processing

The pipeline supports batch processing to amortize model loading costs:
- Recommended batch size: 50-100 documents
- Memory usage scales with batch size and number of unique entities

### Semantic Deduplication

Cosine similarity computation is O(n²) for n facts per entity:
- For entities with 100s of facts, consider sampling or hierarchical clustering
- Typical entity has 5-20 unique facts after deduplication

## Testing

Run the unit tests:

```bash
# Unit tests (with mocked models, fast)
pytest tests/unit/stage4/test_entity_summarization.py

# Integration tests (with real models, slow)
pytest tests/unit/stage4/test_entity_summarization.py -m integration
```

## Advanced Usage

### Custom Entity Extraction

By default, the pipeline uses simple keyword matching to filter entity-relevant sentences. For production, integrate Named Entity Recognition (NER):

```python
from src.stage4.entity_summarization import FactAggregator
import spacy

class NERFactAggregator(FactAggregator):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.nlp = spacy.load("en_core_web_sm")

    def _filter_entity_sentences(self, sentences, entity_name):
        """Use NER to filter entity-relevant sentences."""
        filtered = []
        for sent in sentences:
            doc = self.nlp(sent)
            for ent in doc.ents:
                if ent.text.lower() in entity_name.lower():
                    filtered.append(sent)
                    break
        return filtered
```

### Custom Citation Formatting

Override the `_embed_citations` method to customize citation style:

```python
from src.stage4.entity_summarization import AbstractiveSummarizer

class CustomCitationSummarizer(AbstractiveSummarizer):
    def _embed_citations(self, summary_text, citations):
        """Custom citation format: superscript footnotes."""
        # Example: "Professor Doe received the award⁽¹⁾"
        for num in sorted(citations.keys()):
            summary_text = summary_text.replace(
                f"[{num}]",
                f"⁽{num}⁾"
            )
        return summary_text
```

## Troubleshooting

### OutOfMemoryError

**Problem**: GPU runs out of memory during summarization

**Solution**:
- Use CPU inference: `device=-1`
- Reduce batch size
- Use a smaller model: `"facebook/bart-base"` instead of `"facebook/bart-large-cnn"`

### Slow Processing

**Problem**: Processing is too slow for large datasets

**Solution**:
- Enable GPU inference: `device=0`
- Increase batch size to amortize model loading
- Use distributed processing with multiple workers

### Poor Deduplication

**Problem**: Too many similar facts are retained

**Solution**:
- Increase `similarity_threshold` (e.g., from 0.85 to 0.90)
- Use a better embedding model: `"sentence-transformers/all-mpnet-base-v2"`

## Roadmap

Future enhancements:

1. **Named Entity Recognition (NER)**: Automatic entity detection and co-reference resolution
2. **Multi-document Summarization**: Cross-entity summarization for related entities
3. **Temporal Fact Verification**: Detect and flag contradictory facts across time
4. **Citation Quality Ranking**: Rank sources by authority and recency
5. **Interactive Summarization**: User-guided summarization with feedback loops

## References

- [Sentence-BERT: Sentence Embeddings using Siamese BERT-Networks](https://arxiv.org/abs/1908.10084)
- [BART: Denoising Sequence-to-Sequence Pre-training](https://arxiv.org/abs/1910.13461)
- [Delta Lake: High-Performance ACID Table Storage](https://delta.io/)
