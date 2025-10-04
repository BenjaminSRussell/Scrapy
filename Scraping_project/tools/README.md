# Pipeline Tools

Utility scripts for pipeline operations and analysis.

---

## Available Tools

### 1. Update Seed URLs

**Script**: `update_seeds.py`

Updates the seed URL file with high-quality URLs discovered during crawling.

**Usage**:
```bash
cd Scraping_project/tools
python update_seeds.py
```

**What it does**:
- Reads Stage 2 validation output
- Filters for high-quality URLs (status 200, text/html)
- Adds new URLs to `data/raw/uconn_urls.csv`
- Avoids duplicates

**Configuration**:
Edit `min_successful_validations` in script to change quality threshold.

---

### 2. Analyze Link Graph

**Script**: `analyze_link_graph.py`

Analyzes the link structure and connectivity of discovered URLs.

**Usage**:
```bash
cd Scraping_project/tools
python analyze_link_graph.py
```

**Output**:
- Link graph statistics
- Hub and authority pages
- Connectivity metrics

---

## Running Tools

All tools should be run from the `Scraping_project/tools` directory:

```bash
cd Scraping_project/tools
python <tool_name>.py
```

---

**Last Updated**: October 4, 2025
