#!/usr/bin/env python

import sys
from pathlib import Path
from datetime import datetime

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.common.storage_manager import get_delta
from src.lakehouse import SeedManager

def seed_real_uconn_urls():

    real_urls = [
        "https://uconn.edu/",
        "https://uconn.edu/about-us/",
        "https://uconn.edu/academics/",
        "https://uconn.edu/admissions/",
        "https://uconn.edu/campus-life/",
        "https://uconn.edu/research/",

        "https://uconn.edu/academics/schools-and-colleges/",
        "https://engineering.uconn.edu/",
        "https://clas.uconn.edu/",
        "https://business.uconn.edu/",

        "https://today.uconn.edu/",
        "https://magazine.uconn.edu/",

        "https://averypoint.uconn.edu/",
        "https://hartford.uconn.edu/",
        "https://stamford.uconn.edu/",
        "https://waterbury.uconn.edu/",

        "https://uconn.edu/admissions/prospective-students/",
        "https://uconn.edu/admissions/tuition-and-costs/",
        "https://uconn.edu/campus-life/living-on-campus/",
        "https://uconn.edu/campus-life/activities-recreation/",
    ]

    print(f"🌱 Seeding {len(real_urls)} real UConn URLs")
    print("=" * 80)

    delta = get_delta()
    seed_manager = SeedManager(delta)

    try:
        result = seed_manager.add_urls_to_seeds(
            urls=real_urls,
            source_url="seed_script",
            source_spider="manual_seed",
            write_uconn_urls=True,
            enqueue_stage2=False
        )

        print(f"\n✅ Successfully seeded:")
        print(f"   - Seed URLs: {result.get('seed_inserted', 0)}")
        print(f"   - UConn URLs: {result.get('uconn_inserted', 0)}")
        print(f"\n📊 URLs are now in Delta Lake and ready for Stage 1 (Scout Spider)")
        print(f"   Location: data/delta_lake/seed_urls/")

        seeds = delta.read_table('seed_urls')
        print(f"\n✅ Verification: {len(seeds)} total URLs in seed_urls table")

        print(f"\n📋 Sample URLs seeded:")
        for i, url in enumerate(real_urls[:5]):
            print(f"   {i+1}. {url}")
        print(f"   ... and {len(real_urls) - 5} more")

        return True

    except Exception as e:
        print(f"❌ Error seeding URLs: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == '__main__':
    success = seed_real_uconn_urls()
    sys.exit(0 if success else 1)
