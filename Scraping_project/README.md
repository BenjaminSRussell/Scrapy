# 🚀 Scraping Project Technical README 🚀

Welcome to the technical deep dive into the UConn Web Scraping Pipeline! This guide is for developers and anyone curious about what makes this project tick. Let's explore the engine room! 🛠️

---

## 🏁 Quick Start for Developers

Get the pipeline running from within the project directory:

```bash
cd Scraping_project
python -m src.orchestrator.main --env development --stage all
```

---

## 🏗️ How It Works: The 3-Stage Pipeline

Our pipeline is a three-act show, transforming seed URLs into enriched, ready-to-use data.

| Stage               | What it Does                               | Engine            |
|---------------------|--------------------------------------------|-------------------|
| 🌐 **1. Discovery**   | Crawls seed URLs to find new links.        | Scrapy + Twisted  |
| ✨ **2. Validation**  | Checks if discovered URLs are valid & live.| aiohttp + asyncio |
| 🧠 **3. Enrichment**  | Extracts valuable info using NLP.          | Scrapy + spaCy    |

---

## ⚙️ Configuration

All settings are neatly organized in the `config/` directory.

- `development.yml`: For your local machine.
- `production.yml`: For deployment.
- `keywords.txt`: For content classification.

### Key Settings Example

Here are some of the levers you can pull:

```yaml
stages:
  discovery:
    max_depth: 3
    seed_file: data/raw/uconn_urls.csv

  validation:
    max_workers: 50
    timeout: 10

  enrichment:
    batch_size: 100

nlp:
  model: en_core_web_sm

queue:
  max_queue_size: 10000
```

---

## 🗺️ Project Tour

Let's take a walk through the project structure.

```
Scraping_project/
├── src/                # 🐍 The Python source code lives here
│   ├── stage1/         # Discovery spider
│   ├── stage2/         # URL validator
│   ├── stage3/         # Content enrichment
│   ├── common/         # Shared utilities for all stages
│   └── orchestrator/   # The conductor of our pipeline
├── tests/              # ✅ All our tests
├── tools/              # 🔨 Utility and helper scripts
├── config/             # ⚙️ Pipeline configurations
└── data/               # 📊 Input and output data
```

---

## 📊 Data Outputs

Each stage produces a specific output file. Here’s what to expect.

### Stage 1: Discovery

- **File:** `data/processed/stage01/discovery_output.jsonl`
- **Content:** A list of all URLs discovered from the initial seeds.

### Stage 2: Validation

- **File:** `data/processed/stage02/validation_output.jsonl`
- **Content:** The status of each URL (e.g., 200 OK, 404 Not Found).

### Stage 3: Enrichment (The Grand Finale!)

- **File:** `data/processed/stage03/enriched_content.jsonl`
- **Content:** The final, enriched data with NLP insights.

```json
{
  "url": "https://uconn.edu/academics",
  "title": "Academics - UConn",
  "entities": ["University of Connecticut"],
  "keywords": ["academics", "programs"],
  "word_count": 1234
}
```

---

## 🚀 Running the Pipeline

You have granular control over the pipeline execution.

### Run a Single Stage

```bash
# Stage 1: Discover URLs
python -m src.orchestrator.main --stage 1

# Stage 2: Validate URLs
python -m src.orchestrator.main --stage 2

# Stage 3: Enrich Content
python -m src.orchestrator.main --stage 3
```

### Run the Full Pipeline

```bash
python -m src.orchestrator.main --stage all
```

---

## ✅ Testing

Ensure everything is running smoothly with our test suite.

```bash
# Run the full test suite
python -m pytest tests/ -v

# Focus on end-to-end tests
python -m pytest tests/test_end_to_end.py -v

# Check test coverage
python -m pytest tests/ --cov=src --cov-report=html
```

---

## 🤯 Troubleshooting

Having issues? Here are some common fixes.

- **Configuration Error?**
  - Double-check your `.yml` files for syntax errors.

- **Out of Memory?**
  - Try reducing `max_workers` in your `development.yml` file.

- **No Visualization?**
  - Make sure you're using a modern terminal like Windows Terminal or PowerShell 7+.

---

## 🙋‍♀️ Support

- **Found a bug?** Report it on GitHub Issues.
- **Need more details?** Check out the `docs/` folder.
- **Want to see examples?** The `tests/` folder is your friend.

---

**Last Updated:** October 4, 2025