#!/usr/bin/env python3
"""
Benchmarking utility for comparing Scrapy vs Async enrichment performance.

Usage:
    python tools/benchmark_enrichment.py --urls 100 --runs 3
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.stage3.async_enrichment import run_async_enrichment


async def benchmark_async_enrichment(
    urls: List[str],
    output_file: str,
    max_concurrency: int
) -> Dict[str, Any]:
    """Benchmark async enrichment processor"""
    start_time = time.time()

    await run_async_enrichment(
        urls=urls,
        output_file=output_file,
        max_concurrency=max_concurrency,
        timeout=30,
        batch_size=100,
        nlp_config={'use_transformers': False}  # Use spaCy for fair comparison
    )

    duration = time.time() - start_time

    # Count results
    result_count = 0
    if Path(output_file).exists():
        with open(output_file) as f:
            result_count = sum(1 for _ in f)

    return {
        'mode': 'async',
        'duration_seconds': duration,
        'urls_processed': result_count,
        'throughput_urls_per_sec': result_count / duration if duration > 0 else 0,
        'max_concurrency': max_concurrency
    }


def benchmark_scrapy_enrichment(
    urls: List[str],
    output_file: str
) -> Dict[str, Any]:
    """Benchmark traditional Scrapy enrichment (for comparison)"""
    # This would require running the actual Scrapy spider
    # For now, return placeholder data

    from scrapy.crawler import CrawlerProcess
    from scrapy.utils.project import get_project_settings

    from src.stage3.enrichment_spider import EnrichmentSpider

    settings = get_project_settings()
    settings.update({
        'ITEM_PIPELINES': {
            'src.stage3.enrichment_pipeline.Stage3Pipeline': 300,
        },
        'STAGE3_OUTPUT_FILE': output_file,
        'LOG_LEVEL': 'WARNING',
        'ROBOTSTXT_OBEY': False,
        'DOWNLOAD_DELAY': 0,
        'CONCURRENT_REQUESTS': 16,
    })

    start_time = time.time()

    process = CrawlerProcess(settings)
    process.crawl(
        EnrichmentSpider,
        urls_list=urls,
        nlp_config={'use_transformers': False}
    )
    process.start()

    duration = time.time() - start_time

    # Count results
    result_count = 0
    if Path(output_file).exists():
        with open(output_file) as f:
            result_count = sum(1 for _ in f)

    return {
        'mode': 'scrapy',
        'duration_seconds': duration,
        'urls_processed': result_count,
        'throughput_urls_per_sec': result_count / duration if duration > 0 else 0,
        'concurrency': 16
    }


def generate_test_urls(count: int) -> List[str]:
    """Generate test URLs for benchmarking"""
    base_urls = [
        "https://uconn.edu/",
        "https://uconn.edu/about/",
        "https://uconn.edu/admissions/",
        "https://uconn.edu/academics/",
        "https://uconn.edu/research/",
        "https://uconn.edu/athletics/",
        "https://uconn.edu/student-life/",
        "https://uconn.edu/faculty/",
        "https://uconn.edu/alumni/",
        "https://uconn.edu/news/"
    ]

    urls = []
    for i in range(count):
        base = base_urls[i % len(base_urls)]
        urls.append(f"{base}page{i}")

    return urls


async def run_benchmark(
    url_count: int,
    runs: int,
    max_concurrency: int,
    mode: str = 'both'
):
    """Run benchmark comparison"""
    print("=" * 80)
    print(f"Enrichment Performance Benchmark")
    print("=" * 80)
    print(f"URLs: {url_count}")
    print(f"Runs: {runs}")
    print(f"Max concurrency (async): {max_concurrency}")
    print("=" * 80)
    print()

    urls = generate_test_urls(url_count)

    async_results = []
    scrapy_results = []

    for run in range(1, runs + 1):
        print(f"\n--- Run {run}/{runs} ---\n")

        # Async enrichment
        if mode in ['async', 'both']:
            output_file = f"data/temp/benchmark_async_run{run}.jsonl"
            Path(output_file).parent.mkdir(parents=True, exist_ok=True)

            print(f"Running async enrichment (concurrency={max_concurrency})...")

            result = await benchmark_async_enrichment(
                urls=urls,
                output_file=output_file,
                max_concurrency=max_concurrency
            )

            async_results.append(result)

            print(f"  Duration: {result['duration_seconds']:.1f}s")
            print(f"  Throughput: {result['throughput_urls_per_sec']:.1f} URLs/sec")
            print(f"  Processed: {result['urls_processed']} URLs")

        # Note: Scrapy benchmarking is commented out as it requires Twisted reactor
        # which can only be started once per process
        # if mode in ['scrapy', 'both']:
        #     print("\nRunning Scrapy enrichment...")
        #     output_file = f"data/temp/benchmark_scrapy_run{run}.jsonl"
        #     Path(output_file).parent.mkdir(parents=True, exist_ok=True)
        #
        #     result = benchmark_scrapy_enrichment(
        #         urls=urls,
        #         output_file=output_file
        #     )
        #
        #     scrapy_results.append(result)
        #     print(f"  Duration: {result['duration_seconds']:.1f}s")
        #     print(f"  Throughput: {result['throughput_urls_per_sec']:.1f} URLs/sec")

    # Calculate averages
    print("\n" + "=" * 80)
    print("BENCHMARK RESULTS")
    print("=" * 80)

    if async_results:
        avg_duration = sum(r['duration_seconds'] for r in async_results) / len(async_results)
        avg_throughput = sum(r['throughput_urls_per_sec'] for r in async_results) / len(async_results)

        print(f"\nAsync Enrichment (concurrency={max_concurrency}):")
        print(f"  Average duration: {avg_duration:.1f}s")
        print(f"  Average throughput: {avg_throughput:.1f} URLs/sec")
        print(f"  Processed: {async_results[0]['urls_processed']} URLs")

    if scrapy_results:
        avg_duration = sum(r['duration_seconds'] for r in scrapy_results) / len(scrapy_results)
        avg_throughput = sum(r['throughput_urls_per_sec'] for r in scrapy_results) / len(scrapy_results)

        print(f"\nScrapy Enrichment:")
        print(f"  Average duration: {avg_duration:.1f}s")
        print(f"  Average throughput: {avg_throughput:.1f} URLs/sec")
        print(f"  Processed: {scrapy_results[0]['urls_processed']} URLs")

    if async_results and scrapy_results:
        async_avg = sum(r['throughput_urls_per_sec'] for r in async_results) / len(async_results)
        scrapy_avg = sum(r['throughput_urls_per_sec'] for r in scrapy_results) / len(scrapy_results)
        speedup = async_avg / scrapy_avg if scrapy_avg > 0 else 0

        print(f"\nSpeedup: {speedup:.1f}x")

    print("\n" + "=" * 80)


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description='Benchmark enrichment performance')
    parser.add_argument(
        '--urls',
        type=int,
        default=100,
        help='Number of URLs to process (default: 100)'
    )
    parser.add_argument(
        '--runs',
        type=int,
        default=3,
        help='Number of benchmark runs (default: 3)'
    )
    parser.add_argument(
        '--concurrency',
        type=int,
        default=50,
        help='Max concurrency for async mode (default: 50)'
    )
    parser.add_argument(
        '--mode',
        choices=['async', 'scrapy', 'both'],
        default='async',
        help='Benchmark mode (default: async)'
    )

    args = parser.parse_args()

    asyncio.run(run_benchmark(
        url_count=args.urls,
        runs=args.runs,
        max_concurrency=args.concurrency,
        mode=args.mode
    ))


if __name__ == '__main__':
    main()
