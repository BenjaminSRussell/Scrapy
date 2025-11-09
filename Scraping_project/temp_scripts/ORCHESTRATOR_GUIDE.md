# Pipeline Orchestrator Guide

## Overview

The Pipeline Orchestrator coordinates all 4 stages of the web scraping pipeline, managing data flow and ensuring proper sequencing.

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                    PIPELINE ORCHESTRATOR                         │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   STAGE 1       │    │   STAGE 2       │    │   STAGE 3       │
│ URL Discovery   │───▶│ Page Analysis   │───▶│ Summarization   │
│  (Scout)        │    │  (Async Worker) │    │ (Quality Docs)  │
└─────────────────┘    └─────────────────┘    └─────────────────┘
        │                      │                        │
        │                      ▼                        ▼
        │              ┌───────────────┐       ┌────────────────┐
        │              │ stage2_page   │       │  stage3        │
        │              │ _analysis     │       │  _summaries    │
        │              └───────────────┘       └────────────────┘
        │                      │
        │                      │ (massive docs)
        │                      ▼
        │              ┌─────────────────┐
        │              │   STAGE 4       │
        └─────────────▶│  Large Docs     │
                       │  (Heavy Models) │
                       └─────────────────┘
                               │
                               ▼
                       ┌──────────────────┐
                       │ stage4_large     │
                       │ _doc_summaries   │
                       └──────────────────┘
```

## Components

### PipelineOrchestrator

Main class that coordinates pipeline execution.

**Methods:**
- `run_stage1()` - URL discovery with Scout spider
- `run_stage2()` - Async page analysis
- `run_stage3()` - Summarization for quality docs
- `run_stage4()` - Large document processing
- `run_full_pipeline()` - Execute all stages in sequence
- `run_stage_by_name()` - Run individual stage by name

### PipelineStats

Dataclass tracking pipeline metrics:
- URLs discovered and queued
- Pages analyzed
- Quality vs massive doc counts
- Summaries created
- Execution duration

## Usage

### Run Full Pipeline

```bash
python temp_scripts/run_orchestrator.py
```

This executes all 4 stages in sequence:
1. Stage 1 discovers URLs and queues them
2. Stage 2 analyzes queued pages
3. Stage 3 & 4 run in parallel:
   - Stage 3 summarizes quality documents
   - Stage 4 processes massive documents

### Run Individual Stages

```bash
# Stage 1 only
python temp_scripts/run_individual_stage.py stage1

# Stage 2 only
python temp_scripts/run_individual_stage.py stage2

# Stage 3 only
python temp_scripts/run_individual_stage.py stage3

# Stage 4 only
python temp_scripts/run_individual_stage.py stage4
```

### Programmatic Usage

```python
from src.orchestrator import PipelineOrchestrator
import asyncio

# Create orchestrator
orchestrator = PipelineOrchestrator()

# Run full pipeline
await orchestrator.run_full_pipeline(
    stage1_url_limit=100,
    stage2_concurrent=50,
    stage3_concurrent=20,
)

# Or run individual stages
orchestrator.run_stage1(url_limit=50)
await orchestrator.run_stage2(max_concurrent=10)
await orchestrator.run_stage3(max_concurrent=5)
await orchestrator.run_stage4()

# Access statistics
print(f"Pages analyzed: {orchestrator.stats.stage2_pages_analyzed}")
print(f"Summaries created: {orchestrator.stats.stage3_summaries_created}")
```

## Configuration

The orchestrator accepts stage-specific configuration:

```python
orchestrator = PipelineOrchestrator(config={
    'stage1': {'spider': 'scout', 'limit': 100},
    'stage2': {'max_concurrent': 50, 'batch_size': 100},
    'stage3': {'max_concurrent': 20, 'batch_size': 50},
    'stage4': {'model_name': 'facebook/bart-large-cnn'},
})
```

## Data Flow

### Stage 1 → Stage 2

Scout spider discovers URLs and yields queue items:

```python
{
    'url': 'https://example.com/page',
    'parent_url': 'https://example.com',
    'content_hint': 'html',
    'status': 'pending',
    'queued_at': '2025-11-09T...',
    'queued_by': 'scout'
}
```

Saved to: `stage2_queue` (Delta Lake table)

### Stage 2 → Stage 3 & 4

Stage 2 analyzes pages and writes results:

```python
{
    'url': 'https://example.com/page',
    'word_count': 1500,
    'text_to_html_ratio': 0.42,
    'is_low_quality': False,
    'is_massive_doc': False,  # or True for Stage 4
    'text_content': '...',
    'analyzed_at': '2025-11-09T...'
}
```

Saved to: `stage2_page_analysis` (Delta Lake table)

**Routing:**
- Quality docs (not massive, not low quality) → Stage 3
- Massive docs (>50K chars) → Stage 4

### Stage 3 Output

Summaries for quality documents:

```python
{
    'url': 'https://example.com/page',
    'summary': 'This page discusses...',
    'word_count': 1500,
    'keywords': ['research', 'data', 'analysis'],
    'timestamp': '2025-11-09T...'
}
```

Saved to: `stage3_summaries` (Delta Lake table)

### Stage 4 Output

Summaries for large documents:

```python
{
    'url': 'https://example.com/large-doc.pdf',
    'summary': 'This document covers...',
    'content_type': 'pdf',
    'original_size': 150000,
    'summary_size': 500,
    'compression_ratio': 0.003,
    'processed_at': '2025-11-09T...'
}
```

Saved to: `stage4_large_doc_summaries` (Delta Lake table)

## Monitoring

The orchestrator logs comprehensive information:

```
2025-11-09 02:00:00 [INFO] STAGE 1: URL DISCOVERY
2025-11-09 02:01:00 [INFO] ✅ Stage 1 complete: 50 URLs queued for Stage 2
2025-11-09 02:01:00 [INFO] STAGE 2: PAGE ANALYSIS
2025-11-09 02:02:30 [INFO] ✅ Stage 2 complete: 45 pages analyzed
2025-11-09 02:02:30 [INFO]    - Quality docs: 40
2025-11-09 02:02:30 [INFO]    - Massive docs: 5
2025-11-09 02:02:30 [INFO] STAGE 3: SUMMARIZATION
2025-11-09 02:02:30 [INFO] STAGE 4: LARGE DOCUMENT PROCESSING
2025-11-09 02:03:45 [INFO] ✅ Stage 3 complete: 38 summaries created
2025-11-09 02:05:20 [INFO] ✅ Stage 4 complete: 4 large doc summaries created
2025-11-09 02:05:20 [INFO] ✅ Total summaries created: 42
```

### Final Statistics

At pipeline completion:

```
================================================================================
PIPELINE EXECUTION COMPLETE
================================================================================
Duration: 320.45 seconds

📊 FINAL STATISTICS:
--------------------------------------------------------------------------------
  Stage 1 (URL Discovery):
    - URLs queued for Stage 2: 50

  Stage 2 (Page Analysis):
    - Pages analyzed: 45
    - Quality docs → Stage 3: 40
    - Massive docs → Stage 4: 5

  Stage 3 (Summarization):
    - Summaries created: 38

  Stage 4 (Large Docs):
    - Large doc summaries: 4
================================================================================
✅ Total summaries created: 42
================================================================================
```

## Performance Optimization

### Parallel Execution

Stages 3 and 4 run in parallel since they read from different data sources:

```python
await asyncio.gather(
    orchestrator.run_stage3(),
    orchestrator.run_stage4(),
)
```

### Concurrency Settings

Tune concurrency for your environment:

```python
# High-throughput (requires good CPU/network)
await orchestrator.run_full_pipeline(
    stage2_concurrent=100,
    stage3_concurrent=50,
)

# Conservative (lower resource usage)
await orchestrator.run_full_pipeline(
    stage2_concurrent=10,
    stage3_concurrent=5,
)
```

## Error Handling

The orchestrator includes comprehensive error handling:

- Individual stage failures don't crash the entire pipeline
- Errors are logged with full context
- Partial results are saved even if some items fail
- Delta Lake provides ACID guarantees for data consistency

## Integration with Existing Components

The orchestrator uses existing workers:
- `Stage2Worker` from `src/stage2/stage2_worker.py`
- `Stage3Worker` from `src/stage3/stage3_worker.py`
- `Stage4Worker` from `src/stage4/stage4_worker.py`

No changes needed to existing code - the orchestrator is a coordination layer.

## Continuous Mode

For production, run workers in continuous mode:

```python
# Stage 2 continuous
from src.stage2.stage2_worker import run_stage2_worker
asyncio.run(run_stage2_worker())

# Stage 3 continuous
from src.stage3.stage3_worker import run_stage3_worker
asyncio.run(run_stage3_worker())

# Stage 4 continuous
from src.stage4.stage4_worker import run_stage4_worker
asyncio.run(run_stage4_worker())
```

Each worker polls for new work periodically.

## Deployment

### Local Testing

```bash
# Run full pipeline once
python temp_scripts/run_orchestrator.py
```

### Production (with Docker)

```bash
# Start all services
docker-compose up -d

# Run orchestrator
docker-compose exec orchestrator python -m src.orchestrator.pipeline_orchestrator
```

### Kubernetes

Deploy each stage as a separate pod:
- Stage 1: CronJob (periodic spider execution)
- Stage 2-4: Deployments (continuous workers)
- Orchestrator: Job (on-demand pipeline execution)

## Troubleshooting

### No URLs in stage2_queue

- Check Scout spider is running
- Verify seed_urls table has URLs
- Check logs for spider errors

### Stage 2 not processing

- Verify stage2_queue has pending items
- Check network connectivity
- Increase timeout settings

### Summaries not created

- Verify stage2_page_analysis has quality docs
- Check model loading (Stage 3/4)
- Review error logs

### Performance issues

- Reduce concurrency settings
- Increase batch sizes
- Use GPU for Stage 3/4 models
- Scale horizontally with multiple workers
