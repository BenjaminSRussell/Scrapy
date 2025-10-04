# 🚀 UConn Web Scraping Pipeline 🚀

Welcome to the UConn Web Scraping Pipeline! This isn't just any scraper; it's a production-ready, three-stage system that brings your data to life with real-time visualizations and NLP enrichment. 🤖

![Tests](https://img.shields.io/badge/tests-8%2F8%20passing-success)
![Python](https://img.shields.io/badge/python-3.11+-blue)
![Status](https://img.shields.io/badge/status-production%20ready-brightgreen)

---

## 🏁 Quick Start

Ready to see the magic happen? Just a few commands and you're off!

```bash
git clone <repo-url>
cd Scrapy
./run_the_scrape
```

**That's it!** The script handles everything from setup to completion. ✅

---

## 🎯 What It Does

This pipeline discovers, validates, and enriches web content with NLP, turning raw data into valuable insights.

```
Seed URLs → 🌐 Discovery → ✨ Validation → 🧠 Enrichment → 📊 Final Output
  (CSV)      (25K URLs)   (22K valid)   (NLP data)   (JSONL)
```

**Your final, enriched data is waiting for you at:** `Scraping_project/data/processed/stage03/enriched_content.jsonl`

---

## 📺 Real-Time Visualization

Why wait for results when you can see the progress live? Our terminal-based visualization keeps you in the loop with smooth animations.

```
================================================================================
  UConn Web Scraping Pipeline | 🚀
================================================================================

  Stage 2 Validation
    [######################------------------]  55.0%
    Processed: 13,938 / 25,342
    Rate: 185 items/s  |  Elapsed: 1m 15s
    ETA: 1m 2s

  Average Rate: 692 items/s
================================================================================
```

---

## ✨ Features

- **📺 Real-time Progress Visualization:** Watch the pipeline work its magic live.
- **⚡️ SQLite Deduplication:** Handles millions of URLs without breaking a sweat.
- **🧠 spaCy NLP Integration:** Automatically extracts entities from content.
- **🔑 Centralized Keyword Config:** Easily manage your search keywords.
- **📝 Structured JSON Logging:** Clean, readable, and machine-parseable logs.
- **🔄 Auto-Resume Checkpoints:** Pick up right where you left off after an interruption.
- **🔒 Type-Safe Configuration:** Ensures your settings are always valid.
- **✅ End-to-End Tests:** 8/8 tests passing for reliable performance.

---

## 🛠️ Usage

Get more control over the pipeline with these commands:

```bash
# Run the full pipeline from start to finish
./run_the_scrape

# Run a specific stage (e.g., stage 1)
./run_the_scrape --stage 1

# Skip the installation if you've already set it up
./run_the_scrape --skip-install
```

---

## 📄 Output Example

Here’s a sneak peek at the beautifully structured data you'll get:

```json
{
  "url": "https://uconn.edu/academics",
  "title": "Academics - UConn",
  "entities": ["University of Connecticut"],
  "keywords": ["academics", "programs"],
  "categories": ["education"],
  "word_count": 1234
}
```

---

## 🚀 Performance

| Metric         | Value          |
|----------------|----------------|
| 🌐 Discovery   | 500+ URLs/min  |
| ✨ Validation  | 185 URLs/sec   |
| 🧠 Enrichment  | 10 pages/min   |
| 💾 Memory      | 2GB RAM        |
| 📥 Queue       | 10,000 items   |

---

## 💻 Stack

| Layer           | Tech               |
|-----------------|--------------------|
| 🕷️ Crawling     | Scrapy             |
| ✅ Validation   | aiohttp            |
| 🧠 NLP          | spaCy              |
| ⚡️ Deduplication| SQLite             |
| 📺 Visualization| ASCII animations   |
| 💾 Storage      | JSONL              |
| 🚚 ETL          | Java (Spring Boot) |

---

## 🏗️ Architecture

This project uses a hybrid Python and Java architecture.

1.  **🐍 Python Scraper (`Scraping_project/`):** A 3-stage Scrapy-based pipeline that discovers, validates, and enriches web content.
2.  **☕ Java ETL Loader (`Scraping_project/java-etl-loader/`):** A Spring Boot app that loads the final JSONL output into a PostgreSQL data warehouse.

> **Note:** The mixed-language approach is a work in progress. We plan to migrate the Java ETL process to Python for a more unified system.

---

## 📚 Documentation

- **[Technical Docs](Scraping_project/README.md):** Dive deep into the architecture.
- **[Data Guide](Scraping_project/data/DATA_README.md):** Understand the output structure.
- **[DevOps Guide](Scraping_project/docs/devops_guide.md):** Learn about our CI/CD setup.

---

## ✅ Testing

We take testing seriously. Here’s how to run the tests:

```bash
cd Scraping_project
python -m pytest tests/ -v

# Run end-to-end tests for the full pipeline
python -m pytest tests/test_end_to_end.py -v
```

**Status:** 8/8 end-to-end tests passing! 💯

---

##📋 Requirements

- Python 3.11+
- Java 17+ & Maven 3.6+
- 4GB RAM minimum
- Windows 10+ / Ubuntu 20.04+ / macOS 11+
- A modern terminal that supports cool animations! 😎

---

## 📈 Recent Updates (2025-10-04)

- [✅] Real-time progress visualization with animations
- [✅] Queue size optimized to 10,000 items
- [✅] Keywords centralized to `config/keywords.txt`
- [✅] SQLite-backed URL deduplication
- [✅] Removed legacy code and TODO comments
- [✅] Fixed visualization rendering
- [✅] All end-to-end tests passing

---

## 🤖 CI/CD

Our pipeline is automated with GitHub Actions, which runs on every push to:
- Lint code with `ruff`
- Run unit and integration tests
- Deploy to production

---

**Last Updated:** October 4, 2025
**Status:** Production Ready ✓