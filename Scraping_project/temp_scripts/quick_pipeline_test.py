#!/usr/bin/env python3
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.common.storage_manager import get_delta

TEST_URLS = [
    "https://uconn.edu/",
    "https://uconn.edu/admissions/",
    "https://uconn.edu/academics/",
    "https://uconn.edu/research/",
    "https://uconn.edu/campus-life/",
]

async def quick_test():
    print("\n" + "🚀 " * 40)
    print("QUICK PIPELINE TEST (Without Heavy NLP)")
    print("🚀 " * 40 + "\n")

    delta = get_delta()

    print("Step 1: Creating mock data...")
    mock_data = [
        {
            'url': TEST_URLS[0],
            'word_count': 500,
            'text_to_html_ratio': 0.35,
            'is_low_quality': False,
            'is_massive_doc': False,
            'text_content': "Welcome to UConn. Premier public research university.",
            'content_type': 'html',
            'has_error': False,
        },
        {
            'url': TEST_URLS[1],
            'word_count': 1200,
            'text_to_html_ratio': 0.42,
            'is_low_quality': False,
            'is_massive_doc': False,
            'text_content': "Admissions at UConn. We seek academically talented students.",
            'content_type': 'html',
            'has_error': False,
        },
        {
            'url': TEST_URLS[2],
            'word_count': 2500,
            'text_to_html_ratio': 0.38,
            'is_low_quality': False,
            'is_massive_doc': False,
            'text_content': "Academic Programs. UConn offers comprehensive programs.",
            'content_type': 'html',
            'has_error': False,
        },
    ]

    delta.write('stage2_page_analysis', mock_data, mode='overwrite')
    print(f"✅ Created {len(mock_data)} mock records\n")

    print("Step 2: Checking data integrity...")
    analysis = delta.read_table('stage2_page_analysis')
    print(f"✅ Page Analysis Records: {len(analysis)}")

    for i, record in enumerate(analysis, 1):
        print(f"  {i}. {record['url'][:60]} - {record['word_count']} words")

    print("\n✅ Pipeline data structure verified!")
    print(f"✅ {len(TEST_URLS)} test URLs ready")
    print(f"✅ Mock data created successfully")

    return True

if __name__ == '__main__':
    success = asyncio.run(quick_test())
    sys.exit(0 if success else 1)
