#!/usr/bin/env python3
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.common.storage_manager import get_delta

async def test_edge_cases():
    print("\n" + "🧪 " * 40)
    print("EDGE CASE & ERROR HANDLING TESTS")
    print("🧪 " * 40 + "\n")

    delta = get_delta()
    results = {}

    print("=" * 80)
    print("TEST 1: Empty Content Handling")
    print("=" * 80)

    try:
        empty_data = [
            {
                'url': 'https://empty.com/',
                'word_count': 0,
                'text_to_html_ratio': 0.0,
                'is_low_quality': True,
                'is_massive_doc': False,
                'text_content': '',
                'content_type': 'html',
                'has_error': False,
            }
        ]

        delta.write('stage2_page_analysis', empty_data, mode='overwrite')

        from src.stage3.stage3_worker import Stage3Worker
        worker = Stage3Worker(max_concurrent=5)

        await worker.run()

        summaries = delta.read_table('stage4_summaries')
        print(f"✅ Handled empty content gracefully")
        print(f"   Summaries: {len(summaries)} (expected: 0 for empty content)")

        results['empty_content'] = True

    except Exception as e:
        print(f"❌ Empty content test failed: {e}")
        results['empty_content'] = False

    print("\n" + "=" * 80)
    print("TEST 2: Very Long URLs")
    print("=" * 80)

    try:
        long_url = "https://example.com/" + "a" * 1000

        long_url_data = [
            {
                'url': long_url,
                'word_count': 500,
                'text_to_html_ratio': 0.3,
                'is_low_quality': False,
                'is_massive_doc': False,
                'text_content': 'Test content for very long URL.',
                'content_type': 'html',
                'has_error': False,
            }
        ]

        delta.write('stage2_page_analysis', long_url_data, mode='overwrite')

        worker = Stage3Worker(max_concurrent=5)
        await worker.run()

        print(f"✅ Handled very long URL (length: {len(long_url)})")

        results['long_urls'] = True

    except Exception as e:
        print(f"❌ Long URL test failed: {e}")
        results['long_urls'] = False

    print("\n" + "=" * 80)
    print("TEST 3: Special Characters in Content")
    print("=" * 80)

    try:
        special_content = "Test with émojis 🎉 and spëcial çharacters αβγ 中文 日本語"

        special_data = [
            {
                'url': 'https://special.com/',
                'word_count': 100,
                'text_to_html_ratio': 0.3,
                'is_low_quality': False,
                'is_massive_doc': False,
                'text_content': special_content * 10,
                'content_type': 'html',
                'has_error': False,
            }
        ]

        delta.write('stage2_page_analysis', special_data, mode='overwrite')

        worker = Stage3Worker(max_concurrent=5)
        await worker.run()

        summaries = delta.read_table('stage4_summaries')
        print(f"✅ Handled special characters and unicode")
        print(f"   Summaries created: {len(summaries)}")

        results['special_chars'] = True

    except Exception as e:
        print(f"❌ Special characters test failed: {e}")
        results['special_chars'] = False

    print("\n" + "=" * 80)
    print("TEST 4: Extremely Large Documents")
    print("=" * 80)

    try:
        huge_content = "This is a very large document. " * 10000

        huge_data = [
            {
                'url': 'https://huge.com/',
                'word_count': 100000,
                'text_to_html_ratio': 0.3,
                'is_low_quality': False,
                'is_massive_doc': True,
                'text_content': huge_content,
                'content_type': 'html',
                'has_error': False,
            }
        ]

        delta.write('stage2_page_analysis', huge_data, mode='overwrite')

        from src.stage4.stage4_worker import Stage4Worker
        worker = Stage4Worker()
        await worker.run()

        large_summaries = delta.read_table('stage4_large_doc_summaries')
        print(f"✅ Handled extremely large document")
        print(f"   Original size: {len(huge_content):,} chars")
        print(f"   Summaries created: {len(large_summaries)}")

        if large_summaries:
            summary_size = len(large_summaries[0].get('summary', ''))
            compression = summary_size / len(huge_content)
            print(f"   Compression: {compression:.6f} ({1/compression:.1f}x)")

        results['huge_documents'] = True

    except Exception as e:
        print(f"❌ Huge document test failed: {e}")
        results['huge_documents'] = False

    print("\n" + "=" * 80)
    print("TEST 5: Mixed Quality Documents")
    print("=" * 80)

    try:
        mixed_data = [
            {
                'url': f'https://mixed{i}.com/',
                'word_count': 100 + (i * 1000),
                'text_to_html_ratio': 0.1 + (i * 0.1),
                'is_low_quality': i % 3 == 0,
                'is_massive_doc': i > 5,
                'text_content': f'Document {i} content. ' * (100 + i * 100),
                'content_type': 'html',
                'has_error': False,
            }
            for i in range(10)
        ]

        delta.write('stage2_page_analysis', mixed_data, mode='overwrite')

        worker3 = Stage3Worker(max_concurrent=5)
        await worker3.run()

        worker4 = Stage4Worker()
        await worker4.run()

        summaries = delta.read_table('stage4_summaries')
        large_summaries = delta.read_table('stage4_large_doc_summaries')

        quality_count = len([d for d in mixed_data if not d['is_low_quality'] and not d['is_massive_doc']])
        massive_count = len([d for d in mixed_data if d['is_massive_doc']])

        print(f"✅ Processed mixed quality documents")
        print(f"   Total documents: {len(mixed_data)}")
        print(f"   Quality docs: {quality_count} → {len(summaries)} summaries")
        print(f"   Massive docs: {massive_count} → {len(large_summaries)} summaries")

        results['mixed_quality'] = True

    except Exception as e:
        print(f"❌ Mixed quality test failed: {e}")
        results['mixed_quality'] = False

    print("\n" + "=" * 80)
    print("TEST 6: Concurrent Processing Stress Test")
    print("=" * 80)

    try:
        stress_data = [
            {
                'url': f'https://stress{i}.com/',
                'word_count': 500 + (i * 10),
                'text_to_html_ratio': 0.3,
                'is_low_quality': False,
                'is_massive_doc': False,
                'text_content': f'Stress test document {i}. ' * 100,
                'content_type': 'html',
                'has_error': False,
            }
            for i in range(50)
        ]

        delta.write('stage2_page_analysis', stress_data, mode='overwrite')

        worker = Stage3Worker(max_concurrent=10)
        await worker.run()

        summaries = delta.read_table('stage4_summaries')
        print(f"✅ Concurrent processing stress test passed")
        print(f"   Processed {len(stress_data)} documents")
        print(f"   Created {len(summaries)} summaries")
        print(f"   Concurrency: 10 workers")

        results['concurrent_stress'] = True

    except Exception as e:
        print(f"❌ Concurrent stress test failed: {e}")
        results['concurrent_stress'] = False

    print("\n" + "=" * 80)
    print("EDGE CASE TEST RESULTS")
    print("=" * 80)

    total = len(results)
    passed = sum(1 for v in results.values() if v)

    for test_name, passed_test in results.items():
        status = "✅ PASS" if passed_test else "❌ FAIL"
        print(f"{status}: {test_name}")

    print(f"\n📊 Score: {passed}/{total} tests passed ({passed/total*100:.1f}%)")

    if passed == total:
        print("\n🎉 ALL EDGE CASE TESTS PASSED! 🎉")
        return True
    else:
        print(f"\n⚠️  {total - passed} tests failed")
        return False

if __name__ == '__main__':
    success = asyncio.run(test_edge_cases())
    sys.exit(0 if success else 1)
