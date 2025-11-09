#!/usr/bin/env python
"""Simplified Stage 2 test - fetch and analyze URLs directly without Delta Lake queuing"""
import asyncio
import aiohttp
import sys
from pathlib import Path
from bs4 import BeautifulSoup
from datetime import datetime

# Add project to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.common.storage_manager import get_delta

async def analyze_url(session, url):
    """Analyze a single URL"""
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
            html = await response.text()

            # Parse with BeautifulSoup
            soup = BeautifulSoup(html, 'html.parser')

            # Extract text
            text = soup.get_text(separator=' ', strip=True)
            word_count = len(text.split())

            # Calculate quality metrics
            html_size = len(html)
            text_size = len(text)
            text_to_html_ratio = text_size / html_size if html_size > 0 else 0

            result = {
                'url': url,
                'status': 'analyzed',
                'word_count': word_count,
                'html_size': html_size,
                'text_size': text_size,
                'text_to_html_ratio': round(text_to_html_ratio, 3),
                'analyzed_at': datetime.now().isoformat(),
                'http_status': response.status,
            }

            print(f"✅ {url[:60]:<60} | {word_count:>5} words | {text_to_html_ratio:.2%} ratio")
            return result

    except Exception as e:
        print(f"❌ {url[:60]:<60} | Error: {e}")
        return {
            'url': url,
            'status': 'error',
            'error': str(e),
            'analyzed_at': datetime.now().isoformat(),
        }

async def main():
    print("=" * 80)
    print("STAGE 2: PAGE ANALYSIS (Simplified Test)")
    print("=" * 80)

    # Get URLs from seed_urls
    delta = get_delta()
    try:
        seeds = delta.read_table('seed_urls')
        urls = [item['url'] for item in seeds[:10]]
        print(f"\n📥 Loaded {len(urls)} URLs from seed_urls\n")
    except Exception as e:
        print(f"⚠️ Error loading seeds: {e}")
        urls = [
            "https://uconn.edu/",
            "https://uconn.edu/about-us/",
            "https://today.uconn.edu/",
        ]
        print(f"📝 Using {len(urls)} fallback URLs\n")

    # Analyze URLs
    async with aiohttp.ClientSession() as session:
        tasks = [analyze_url(session, url) for url in urls]
        results = await asyncio.gather(*tasks)

    # Save to Delta Lake
    valid_results = [r for r in results if r.get('status') == 'analyzed']

    if valid_results:
        print(f"\n📊 Successfully analyzed {len(valid_results)} pages")
        try:
            delta.write('stage2_page_analysis', valid_results, mode='append')
            print(f"✅ Saved {len(valid_results)} results to stage2_page_analysis")

            # Show summary
            total_words = sum(r.get('word_count', 0) for r in valid_results)
            avg_words = total_words / len(valid_results)
            print(f"\n📈 Statistics:")
            print(f"   Total words: {total_words:,}")
            print(f"   Average words/page: {avg_words:.0f}")

        except Exception as e:
            print(f"⚠️ Error saving to Delta Lake: {e}")
    else:
        print("\n⚠️ No valid results to save")

    print("\n✅ Stage 2 test complete!")

if __name__ == '__main__':
    asyncio.run(main())
