"""
Standalone Link Graph Analysis Script

Analyzes the link graph from discovered URLs and calculates importance scores.
Run this AFTER Stage 1 discovery completes.

Usage:
    python tools/analyze_link_graph.py
    python tools/analyze_link_graph.py --input data/processed/stage01/discovery_output.jsonl
"""

import argparse
import json
import logging
from collections import defaultdict
from pathlib import Path

from src.common.link_graph import LinkGraphAnalyzer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def analyze_link_graph(discovery_file: Path, graph_db: Path):
    """Analyze link graph from discovery output."""

    if not discovery_file.exists():
        logger.error(f"Discovery file not found: {discovery_file}")
        return

    logger.info(f"Loading discovered URLs from {discovery_file}")

    page_outlinks = defaultdict(set)
    page_depths = {}
    url_count = 0

    with open(discovery_file, encoding='utf-8') as f:
        for line_no, line in enumerate(f, 1):
            try:
                data = json.loads(line.strip())
                source_url = data.get('source_url')
                discovered_url = data.get('discovered_url')
                depth = data.get('discovery_depth', 0)

                if source_url and discovered_url:
                    page_outlinks[source_url].add(discovered_url)

                    if source_url not in page_depths:
                        page_depths[source_url] = max(0, depth - 1)
                    if discovered_url not in page_depths:
                        page_depths[discovered_url] = depth

                    url_count += 1

                if line_no % 10000 == 0:
                    logger.info(f"Processed {line_no:,} lines, {len(page_outlinks):,} pages")

            except json.JSONDecodeError as e:
                logger.warning(f"Invalid JSON at line {line_no}: {e}")
                continue

    logger.info(f"Loaded {url_count:,} URL relationships from {len(page_outlinks):,} pages")

    link_graph = LinkGraphAnalyzer(graph_db)

    logger.info("Building link graph...")
    for source_url, outlinks in page_outlinks.items():
        depth = page_depths.get(source_url, 0)
        link_graph.add_page(source_url, list(outlinks), depth=depth)

    logger.info(f"Added {len(page_outlinks):,} pages to link graph")

    logger.info("Calculating PageRank scores...")
    pagerank_scores = link_graph.calculate_pagerank()
    logger.info(f"Calculated PageRank for {len(pagerank_scores):,} URLs")

    logger.info("Calculating HITS (hub/authority) scores...")
    hub_scores, authority_scores = link_graph.calculate_hits()
    logger.info(f"Calculated HITS scores for {len(hub_scores):,} URLs")

    stats = link_graph.get_graph_stats()

    logger.info("=" * 60)
    logger.info("LINK GRAPH STATISTICS:")
    logger.info(f"  Total nodes: {stats.total_nodes:,}")
    logger.info(f"  Total edges: {stats.total_edges:,}")
    logger.info(f"  Average degree: {stats.avg_degree:.2f}")
    logger.info(f"  Max degree: {stats.max_degree}")

    if stats.top_pages_by_pagerank:
        logger.info("\nTop 10 pages by PageRank:")
        for i, (url, score) in enumerate(stats.top_pages_by_pagerank[:10], 1):
            logger.info(f"  {i}. {score:.4f} - {url}")

    if stats.top_authorities:
        logger.info("\nTop 10 authorities (HITS):")
        for i, (url, score) in enumerate(stats.top_authorities[:10], 1):
            logger.info(f"  {i}. {score:.4f} - {url}")

    if stats.top_hubs:
        logger.info("\nTop 10 hubs (HITS):")
        for i, (url, score) in enumerate(stats.top_hubs[:10], 1):
            logger.info(f"  {i}. {score:.4f} - {url}")

    logger.info("=" * 60)
    logger.info(f"Link graph analysis complete. Database saved to: {graph_db}")


def main():
    parser = argparse.ArgumentParser(description="Analyze link graph from discovery output")
    parser.add_argument(
        '--input',
        type=Path,
        default=Path('data/processed/stage01/discovery_output.jsonl'),
        help='Discovery output JSONL file'
    )
    parser.add_argument(
        '--graph-db',
        type=Path,
        default=Path('data/processed/link_graph.db'),
        help='Link graph database file'
    )

    args = parser.parse_args()

    analyze_link_graph(args.input, args.graph_db)


if __name__ == '__main__':
    main()
