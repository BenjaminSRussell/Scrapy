#!/usr/bin/env python3
import asyncio
import sys
import subprocess
import time
import requests
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.common.storage_manager import get_delta
from src.stage2.stage2_worker import Stage2Worker

MOCK_SERVER_PORT = 8888
MOCK_URLS = [
    f"http://localhost:{MOCK_SERVER_PORT}/",
    f"http://localhost:{MOCK_SERVER_PORT}/admissions/",
    f"http://localhost:{MOCK_SERVER_PORT}/academics/",
    f"http://localhost:{MOCK_SERVER_PORT}/research/",
    f"http://localhost:{MOCK_SERVER_PORT}/campus-life/",
]

def start_mock_server():
    script_path = Path(__file__).parent / "mock_http_server.py"
    process = subprocess.Popen(
        [sys.executable, str(script_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    time.sleep(2)

    try:
        response = requests.get(f"http://localhost:{MOCK_SERVER_PORT}/", timeout=2)
        if response.status_code == 200:
            print(f"✅ Mock server started on port {MOCK_SERVER_PORT}")
            return process
    except:
        pass

    print(f"❌ Failed to start mock server")
    return None

async def test_with_mock_http():
    print("\n" + "🌐 " * 40)
    print("TEST WITH MOCK HTTP SERVER (DNS Workaround)")
    print("🌐 " * 40 + "\n")

    mock_server = start_mock_server()

    if not mock_server:
        print("❌ Cannot proceed without mock server")
        return False

    try:
        delta = get_delta()
        results = {}

        print("=" * 80)
        print("TEST 1: Seed URLs with Mock Server")
        print("=" * 80)

        queue_items = []
        for url in MOCK_URLS:
            queue_items.append({
                'url': url,
                'status': 'pending',
                'content_hint': 'html',
                'queued_at': '2025-11-09T12:00:00',
            })

        delta.write('stage2_queue', queue_items, mode='overwrite')
        print(f"✅ Seeded {len(queue_items)} URLs to stage2_queue")

        queue = delta.read_table('stage2_queue')
        pending = [item for item in queue if item.get('status') == 'pending']
        print(f"✅ Verified {len(pending)} pending items in queue")

        results['seed_urls'] = True

        print("\n" + "=" * 80)
        print("TEST 2: Stage 2 Worker with Real HTTP")
        print("=" * 80)

        worker = Stage2Worker(max_concurrent=5, batch_size=10)

        print(f"📊 Starting Stage 2 worker...")
        await worker.run()

        analysis = delta.read_table('stage2_page_analysis')
        print(f"✅ Stage 2 completed")
        print(f"✅ Analyzed {len(analysis)} pages")

        if analysis:
            print(f"\n📋 Analysis Results:")
            for i, item in enumerate(analysis[:5], 1):
                url = item.get('url', 'N/A')
                wc = item.get('word_count', 0)
                ratio = item.get('text_to_html_ratio', 0)
                quality = "✓" if not item.get('is_low_quality', False) else "✗"
                print(f"  {i}. {url}")
                print(f"     Words: {wc}, Ratio: {ratio:.3f}, Quality: {quality}")

        results['stage2_worker'] = len(analysis) > 0

        print("\n" + "=" * 80)
        print("TEST 3: Verify Real HTTP Fetching")
        print("=" * 80)

        has_content = False
        for item in analysis:
            text = item.get('text_content', '')
            if len(text) > 100:
                has_content = True
                print(f"✅ Real content fetched: {len(text)} chars")
                print(f"   Preview: {text[:100]}...")
                break

        results['real_http'] = has_content

        print("\n" + "=" * 80)
        print("TEST RESULTS")
        print("=" * 80)

        total = len(results)
        passed = sum(1 for v in results.values() if v)

        for test_name, passed_test in results.items():
            status = "✅ PASS" if passed_test else "❌ FAIL"
            print(f"{status}: {test_name}")

        print(f"\n📊 Score: {passed}/{total} tests passed ({passed/total*100:.1f}%)")

        if passed == total:
            print("\n🎉 DNS WORKAROUND SUCCESSFUL! 🎉")
            print("The pipeline can fetch real HTTP content using mock server")

        return passed == total

    finally:
        if mock_server:
            print("\n🛑 Stopping mock server...")
            mock_server.terminate()
            mock_server.wait()
            print("✅ Mock server stopped")

if __name__ == '__main__':
    success = asyncio.run(test_with_mock_http())
    sys.exit(0 if success else 1)
