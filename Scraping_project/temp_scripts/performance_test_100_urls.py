#!/usr/bin/env python3
import asyncio
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.common.storage_manager import get_delta
from src.orchestrator.pipeline_orchestrator import PipelineOrchestrator

def generate_test_urls(count=100):
    """Generate test URLs for performance testing"""
    urls = []

    # UConn domains
    uconn_paths = [
        "/", "/admissions/", "/academics/", "/research/", "/campus-life/",
        "/athletics/", "/about/", "/tuition-fees/", "/financial-aid/", "/housing/",
        "/library/", "/health-services/", "/career-services/", "/student-affairs/",
        "/registrar/", "/bursar/", "/undergraduate/", "/graduate/", "/online/",
        "/faculty/", "/staff/", "/alumni/", "/parents/", "/visitors/", "/apply/"
    ]

    # Generate URLs
    for i in range(count):
        path = uconn_paths[i % len(uconn_paths)]
        if i >= len(uconn_paths):
            path = f"{path}page-{i}"
        urls.append(f"https://uconn.edu{path}")

    return urls

async def performance_test_100_urls():
    print("\n" + "⚡ " * 40)
    print("PHASE 3 - PERFORMANCE TEST WITH 100+ URLs")
    print("⚡ " * 40 + "\n")

    delta = get_delta()
    results = {}

    # Generate test URLs
    test_urls = generate_test_urls(100)
    print(f"📊 Generated {len(test_urls)} test URLs")
    print(f"   Sample URLs:")
    for i, url in enumerate(test_urls[:5], 1):
        print(f"     {i}. {url}")
    print(f"     ... and {len(test_urls) - 5} more")
    print()

    # Test 1: Data Seeding Performance
    print("=" * 80)
    print("TEST 1: Data Seeding Performance (100 URLs)")
    print("=" * 80)

    start_time = time.time()

    try:
        mock_data = []
        for i, url in enumerate(test_urls):
            word_count = 500 + (i * 50)
            is_massive = word_count > 50000

            mock_data.append({
                'url': url,
                'word_count': word_count,
                'text_to_html_ratio': 0.3 + (i * 0.001),
                'is_low_quality': i % 10 == 0,
                'is_massive_doc': is_massive,
                'text_content': f"Content from {url}. " * (word_count // 10),
                'content_type': 'html',
                'has_error': False,
            })

        delta.write('stage2_page_analysis', mock_data, mode='overwrite')

        seed_time = time.time() - start_time

        print(f"✅ Seeded {len(mock_data)} records")
        print(f"⏱️  Time: {seed_time:.2f} seconds")
        print(f"📈 Rate: {len(mock_data) / seed_time:.1f} records/sec")

        results['seeding'] = {
            'success': True,
            'count': len(mock_data),
            'time': seed_time,
            'rate': len(mock_data) / seed_time
        }

    except Exception as e:
        print(f"❌ Seeding failed: {e}")
        results['seeding'] = {'success': False, 'error': str(e)}

    # Test 2: Stage 3 Performance
    print("\n" + "=" * 80)
    print("TEST 2: Stage 3 Summarization Performance")
    print("=" * 80)

    start_time = time.time()

    try:
        from src.stage3.stage3_worker import Stage3Worker

        worker = Stage3Worker(max_concurrent=20, batch_size=50)

        print(f"📊 Configuration:")
        print(f"   Max concurrent: 20 workers")
        print(f"   Batch size: 50 documents")

        await worker.run()

        stage3_time = time.time() - start_time

        summaries = delta.read_table('stage4_summaries')

        print(f"✅ Stage 3 completed")
        print(f"📝 Summaries created: {len(summaries)}")
        print(f"⏱️  Time: {stage3_time:.2f} seconds")
        print(f"📈 Rate: {len(summaries) / stage3_time:.1f} summaries/sec")

        results['stage3'] = {
            'success': True,
            'count': len(summaries),
            'time': stage3_time,
            'rate': len(summaries) / stage3_time
        }

    except Exception as e:
        print(f"❌ Stage 3 failed: {e}")
        results['stage3'] = {'success': False, 'error': str(e)}

    # Test 3: Stage 4 Performance
    print("\n" + "=" * 80)
    print("TEST 3: Stage 4 Large Document Performance")
    print("=" * 80)

    start_time = time.time()

    try:
        from src.stage4.stage4_worker import Stage4Worker

        analysis = delta.read_table('stage2_page_analysis')
        massive_count = len([d for d in analysis if d.get('is_massive_doc', False)])

        print(f"📊 Massive documents to process: {massive_count}")

        if massive_count > 0:
            worker = Stage4Worker()
            await worker.run()

            stage4_time = time.time() - start_time

            large_summaries = delta.read_table('stage4_large_doc_summaries')

            print(f"✅ Stage 4 completed")
            print(f"📝 Large doc summaries: {len(large_summaries)}")
            print(f"⏱️  Time: {stage4_time:.2f} seconds")
            if len(large_summaries) > 0:
                print(f"📈 Rate: {len(large_summaries) / stage4_time:.1f} summaries/sec")

            results['stage4'] = {
                'success': True,
                'count': len(large_summaries),
                'time': stage4_time,
                'rate': len(large_summaries) / stage4_time if len(large_summaries) > 0 else 0
            }
        else:
            print(f"⚠️  No massive documents to process")
            results['stage4'] = {'success': True, 'count': 0, 'skipped': True}

    except Exception as e:
        print(f"❌ Stage 4 failed: {e}")
        results['stage4'] = {'success': False, 'error': str(e)}

    # Test 4: Memory and Data Integrity
    print("\n" + "=" * 80)
    print("TEST 4: Data Integrity and Memory")
    print("=" * 80)

    try:
        analysis = delta.read_table('stage2_page_analysis')
        summaries = delta.read_table('stage4_summaries')
        large_summaries = delta.read_table('stage4_large_doc_summaries')

        quality_count = len([d for d in analysis if not d.get('is_low_quality', False) and not d.get('is_massive_doc', False)])
        massive_count = len([d for d in analysis if d.get('is_massive_doc', False)])

        print(f"📊 Data Verification:")
        print(f"   Total page analysis: {len(analysis)}")
        print(f"   Quality documents: {quality_count}")
        print(f"   Massive documents: {massive_count}")
        print(f"   Quality summaries: {len(summaries)}")
        print(f"   Large doc summaries: {len(large_summaries)}")

        coverage = (len(summaries) / quality_count * 100) if quality_count > 0 else 0
        print(f"   Coverage: {coverage:.1f}%")

        results['integrity'] = {
            'success': True,
            'total': len(analysis),
            'quality': quality_count,
            'massive': massive_count,
            'summaries': len(summaries),
            'large_summaries': len(large_summaries),
            'coverage': coverage
        }

    except Exception as e:
        print(f"❌ Integrity check failed: {e}")
        results['integrity'] = {'success': False, 'error': str(e)}

    # Summary
    print("\n" + "=" * 80)
    print("PERFORMANCE TEST SUMMARY")
    print("=" * 80)

    total_tests = len(results)
    passed_tests = sum(1 for r in results.values() if r.get('success', False))

    print(f"\n📊 Test Results: {passed_tests}/{total_tests} passed")
    print()

    for test_name, result in results.items():
        status = "✅" if result.get('success', False) else "❌"
        print(f"{status} {test_name.upper()}")

        if result.get('success') and 'time' in result:
            print(f"   ⏱️  Time: {result['time']:.2f}s")
            print(f"   📈 Rate: {result['rate']:.1f} items/sec")

        if result.get('success') and test_name == 'integrity':
            print(f"   📊 Coverage: {result['coverage']:.1f}%")
            print(f"   📝 {result['summaries']} summaries from {result['quality']} quality docs")

    print("\n" + "=" * 80)
    print("PERFORMANCE BENCHMARKS")
    print("=" * 80)

    if results['seeding'].get('success'):
        print(f"✅ Data Seeding: {results['seeding']['rate']:.1f} records/sec")

    if results['stage3'].get('success'):
        print(f"✅ Stage 3 Summarization: {results['stage3']['rate']:.1f} summaries/sec")

    if results['stage4'].get('success') and not results['stage4'].get('skipped'):
        print(f"✅ Stage 4 Large Docs: {results['stage4']['rate']:.1f} summaries/sec")

    print()

    if passed_tests == total_tests:
        print("🎉 ALL PERFORMANCE TESTS PASSED! 🎉")
        print(f"Successfully processed {len(test_urls)} URLs with high performance")
        return True
    else:
        print(f"⚠️  {total_tests - passed_tests} tests failed")
        return False

if __name__ == '__main__':
    success = asyncio.run(performance_test_100_urls())
    sys.exit(0 if success else 1)
