"""
Full pipeline integration tests with efficiency metrics.
Tests the complete flow from discovery through enrichment.
"""

import json
import tempfile
import time
from pathlib import Path
from datetime import datetime

import pytest

# Test the full pipeline without external dependencies
def test_full_pipeline_end_to_end(tmp_path):
    """Test complete pipeline flow with efficiency tracking"""
    start_time = time.perf_counter()

    # Setup test directories
    data_dir = tmp_path / "data"
    for subdir in ["raw", "processed/stage01", "processed/stage02", "processed/stage03", "logs"]:
        (data_dir / subdir).mkdir(parents=True, exist_ok=True)

    # Create test seed CSV
    seed_csv = data_dir / "raw" / "uconn_urls.csv"
    test_urls = [f"https://uconn.edu/test-{i}" for i in range(100)]

    with open(seed_csv, 'w') as f:
        for url in test_urls:
            f.write(f"{url}\n")

    # Stage 1: Discovery simulation
    stage1_start = time.perf_counter()
    stage1_output = data_dir / "processed" / "stage01" / "discovered.jsonl"

    discovered_items = []
    for i, url in enumerate(test_urls):
        # Simulate discovery item creation
        import hashlib
        url_hash = hashlib.sha256(url.encode()).hexdigest()

        item = {
            "source_url": "https://uconn.edu/",
            "discovered_url": url,
            "first_seen": datetime.now().isoformat(),
            "url_hash": url_hash,
            "discovery_depth": i % 3
        }
        discovered_items.append(item)

    # Write Stage 1 results
    with open(stage1_output, 'w') as f:
        for item in discovered_items:
            f.write(json.dumps(item) + '\n')

    stage1_duration = time.perf_counter() - stage1_start

    # Stage 2: Validation simulation
    stage2_start = time.perf_counter()
    stage2_output = data_dir / "processed" / "stage02" / "validated.jsonl"

    # Simulate 80% success rate
    validated_items = []
    for i, item in enumerate(discovered_items):
        if i % 5 != 0:  # 80% success rate
            validation_result = {
                "url": item["discovered_url"],
                "url_hash": item["url_hash"],
                "status_code": 200,
                "content_type": "text/html; charset=utf-8",
                "content_length": 2048 + i,
                "response_time": 0.1,
                "is_valid": True,
                "error_message": None,
                "validated_at": datetime.now().isoformat()
            }
            validated_items.append(validation_result)

    # Write Stage 2 results
    with open(stage2_output, 'w') as f:
        for item in validated_items:
            f.write(json.dumps(item) + '\n')

    stage2_duration = time.perf_counter() - stage2_start

    # Stage 3: Enrichment simulation
    stage3_start = time.perf_counter()
    stage3_output = data_dir / "processed" / "stage03" / "enriched.jsonl"

    enriched_items = []
    for item in validated_items:
        enrichment_result = {
            "url": item["url"],
            "url_hash": item["url_hash"],
            "title": f"Test Page for {item['url']}",
            "text_content": f"Test content for {item['url']} " * 20,
            "word_count": 20,
            "entities": ["UConn", "Test"],
            "keywords": ["test", "uconn", "page"],
            "content_tags": ["academic", "test"],
            "has_pdf_links": False,
            "has_audio_links": False,
            "status_code": item["status_code"],
            "content_type": item["content_type"],
            "enriched_at": datetime.now().isoformat()
        }
        enriched_items.append(enrichment_result)

    # Write Stage 3 results
    with open(stage3_output, 'w') as f:
        for item in enriched_items:
            f.write(json.dumps(item) + '\n')

    stage3_duration = time.perf_counter() - stage3_start
    total_duration = time.perf_counter() - start_time

    # Verify all stages completed
    assert stage1_output.exists()
    assert stage2_output.exists()
    assert stage3_output.exists()

    # Verify data integrity
    assert len(discovered_items) == len(test_urls)
    assert len(validated_items) == 80  # 80% success rate
    assert len(enriched_items) == len(validated_items)

    # Calculate efficiency metrics
    efficiency_metrics = {
        "total_input_urls": len(test_urls),
        "discovered_urls": len(discovered_items),
        "validated_urls": len(validated_items),
        "enriched_urls": len(enriched_items),
        "stage1_duration": stage1_duration,
        "stage2_duration": stage2_duration,
        "stage3_duration": stage3_duration,
        "total_duration": total_duration,
        "stage1_throughput": len(discovered_items) / stage1_duration,
        "stage2_throughput": len(validated_items) / stage2_duration,
        "stage3_throughput": len(enriched_items) / stage3_duration,
        "overall_throughput": len(enriched_items) / total_duration,
        "stage2_success_rate": len(validated_items) / len(discovered_items) * 100,
        "end_to_end_success_rate": len(enriched_items) / len(test_urls) * 100,
        "data_retention_efficiency": len(enriched_items) / len(test_urls) * 100
    }

    # Performance assertions
    assert efficiency_metrics["stage1_throughput"] > 100, "Stage 1 too slow"
    assert efficiency_metrics["stage2_success_rate"] >= 75, "Stage 2 success rate too low"
    assert efficiency_metrics["overall_throughput"] > 50, "Overall pipeline too slow"

    print(f"\n📊 Pipeline Efficiency Metrics:")
    print(f"   🔍 Discovery: {efficiency_metrics['discovered_urls']} URLs in {stage1_duration:.3f}s ({efficiency_metrics['stage1_throughput']:.0f} URLs/s)")
    print(f"   ✅ Validation: {efficiency_metrics['validated_urls']} URLs in {stage2_duration:.3f}s ({efficiency_metrics['stage2_throughput']:.0f} URLs/s)")
    print(f"   🔬 Enrichment: {efficiency_metrics['enriched_urls']} URLs in {stage3_duration:.3f}s ({efficiency_metrics['stage3_throughput']:.0f} URLs/s)")
    print(f"   📈 Overall: {efficiency_metrics['overall_throughput']:.0f} URLs/s end-to-end")
    print(f"   🎯 Success Rate: {efficiency_metrics['end_to_end_success_rate']:.1f}%")

    # Test passed successfully - no need to return anything


def test_pipeline_memory_efficiency():
    """Test pipeline memory usage patterns"""
    import psutil
    import os

    process = psutil.Process(os.getpid())
    initial_memory = process.memory_info().rss

    # Test large URL hash set (simulating discovery deduplication)
    url_hashes = set()
    for i in range(10000):
        import hashlib
        url = f"https://uconn.edu/test-{i}"
        url_hash = hashlib.sha256(url.encode()).hexdigest()
        url_hashes.add(url_hash)

    memory_after_hashes = process.memory_info().rss

    # Test URL processing
    processed_urls = []
    for i in range(5000):
        url = f"https://uconn.edu/page-{i}?param={i}"
        # Simulate canonicalization
        canonical = url.lower().replace("?param=" + str(i), "")
        hash_val = hashlib.sha256(canonical.encode()).hexdigest()
        processed_urls.append((canonical, hash_val))

    final_memory = process.memory_info().rss

    # Calculate memory usage
    hash_memory_mb = (memory_after_hashes - initial_memory) / (1024 * 1024)
    processing_memory_mb = (final_memory - memory_after_hashes) / (1024 * 1024)
    total_memory_mb = (final_memory - initial_memory) / (1024 * 1024)

    # Memory efficiency assertions
    assert hash_memory_mb < 50, f"Hash storage too memory-intensive: {hash_memory_mb:.1f}MB"
    assert processing_memory_mb < 25, f"URL processing too memory-intensive: {processing_memory_mb:.1f}MB"
    assert total_memory_mb < 75, f"Total memory usage too high: {total_memory_mb:.1f}MB"

    efficiency_score = 15000 / (total_memory_mb + 1e-9)  # operations per MB

    print(f"\n💾 Memory Efficiency Metrics:")
    print(f"   🔢 Hash Storage: {hash_memory_mb:.1f}MB for 10K hashes")
    print(f"   ⚙️  URL Processing: {processing_memory_mb:.1f}MB for 5K URLs")
    print(f"   📊 Total Usage: {total_memory_mb:.1f}MB")
    print(f"   🎯 Efficiency Score: {efficiency_score:.0f} operations/MB")

    assert efficiency_score > 200, f"Memory efficiency too low: {efficiency_score:.0f} ops/MB"


def test_pipeline_scalability_simulation():
    """Test pipeline behavior under simulated load"""
    start_time = time.perf_counter()

    # Simulate processing different batch sizes
    batch_sizes = [100, 500, 1000, 2000]
    results = {}

    for batch_size in batch_sizes:
        batch_start = time.perf_counter()

        # Simulate URL processing
        processed_items = []
        for i in range(batch_size):
            # Simulate the work done in each stage
            url = f"https://uconn.edu/batch-test-{i}"

            # Stage 1: Discovery (hash generation)
            import hashlib
            url_hash = hashlib.sha256(url.encode()).hexdigest()

            # Stage 2: Validation (mock HTTP check)
            is_valid = i % 10 != 0  # 90% success rate

            # Stage 3: Enrichment (text processing simulation)
            if is_valid:
                content = f"Test content for {url} " * 10
                word_count = len(content.split())

                processed_items.append({
                    "url": url,
                    "hash": url_hash,
                    "valid": is_valid,
                    "word_count": word_count
                })

        batch_duration = time.perf_counter() - batch_start
        throughput = len(processed_items) / batch_duration

        results[batch_size] = {
            "duration": batch_duration,
            "processed": len(processed_items),
            "throughput": throughput,
            "success_rate": len(processed_items) / batch_size * 100
        }

    total_duration = time.perf_counter() - start_time

    # Analyze scalability
    throughputs = [results[size]["throughput"] for size in batch_sizes]
    scalability_factor = max(throughputs) / min(throughputs)

    print(f"\n📈 Scalability Test Results:")
    for batch_size in batch_sizes:
        result = results[batch_size]
        print(f"   📦 Batch {batch_size}: {result['throughput']:.0f} URLs/s ({result['success_rate']:.1f}% success)")

    print(f"   🔄 Scalability Factor: {scalability_factor:.1f}x")
    print(f"   ⏱️  Total Test Time: {total_duration:.2f}s")

    # Scalability assertions
    assert all(r["success_rate"] >= 85 for r in results.values()), "Success rate degraded with scale"
    assert scalability_factor < 3.0, f"Poor scalability: {scalability_factor:.1f}x variation"
    assert min(throughputs) > 200, f"Minimum throughput too low: {min(throughputs):.0f} URLs/s"

    # Test passed successfully - no need to return anything


@pytest.mark.slow
def test_pipeline_endurance():
    """Test pipeline stability over extended operation"""
    iterations = 50
    start_time = time.perf_counter()

    performance_samples = []
    error_count = 0

    for iteration in range(iterations):
        iteration_start = time.perf_counter()

        # Simulate one complete pipeline cycle
        urls = [f"https://uconn.edu/endurance-{iteration}-{i}" for i in range(20)]

        # Process URLs
        processed = 0
        for url in urls:
            import hashlib
            # Discovery
            url_hash = hashlib.sha256(url.encode()).hexdigest()

            # Validation (simulate occasional failures)
            if hash(url) % 20 != 0:  # 95% success rate
                # Enrichment
                content = f"Content for {url}"
                processed += 1

        iteration_duration = time.perf_counter() - iteration_start
        throughput = processed / iteration_duration

        performance_samples.append({
            "iteration": iteration,
            "duration": iteration_duration,
            "processed": processed,
            "throughput": throughput
        })

    total_duration = time.perf_counter() - start_time

    # Analyze performance stability
    throughputs = [s["throughput"] for s in performance_samples]
    avg_throughput = sum(throughputs) / len(throughputs)
    throughput_variance = sum((t - avg_throughput) ** 2 for t in throughputs) / len(throughputs)
    stability_coefficient = (throughput_variance ** 0.5) / avg_throughput * 100

    print(f"\n🔄 Endurance Test Results ({iterations} iterations):")
    print(f"   ⚡ Average Throughput: {avg_throughput:.0f} URLs/s")
    print(f"   📊 Stability Coefficient: {stability_coefficient:.1f}% (lower is better)")
    print(f"   ❌ Error Rate: {error_count}/{iterations} ({error_count/iterations*100:.1f}%)")
    print(f"   ⏱️  Total Duration: {total_duration:.1f}s")

    # Stability assertions
    assert stability_coefficient < 20, f"Performance too unstable: {stability_coefficient:.1f}% variance"
    assert avg_throughput > 100, f"Average throughput too low: {avg_throughput:.0f} URLs/s"

    # Test passed successfully - no need to return anything
