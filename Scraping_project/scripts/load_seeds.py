
import logging
from pathlib import Path
import sys

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.common.delta_lake import get_delta_manager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def load_seeds():
    """
    Loads seed URLs from a CSV file, cleans them, and merges with existing seed_urls table.
    Only adds non-duplicate URLs.
    """
    seed_file_path = Path(__file__).parent.parent / "data" / "raw" / "uconn_urls.csv"
    if not seed_file_path.exists():
        logger.error(f"Seed file not found at: {seed_file_path}")
        return

    delta_manager = get_delta_manager()

    # Load existing seed URLs
    existing_urls = set()
    try:
        existing_records = delta_manager.read('seed_urls')
        existing_urls = {record['url'] for record in existing_records}
        logger.info(f"Found {len(existing_urls)} existing seed URLs in Delta Lake.")
    except Exception as e:
        logger.info(f"No existing seed_urls table found, will create new one. ({e})")

    # Load URLs from CSV
    new_urls = []
    duplicate_count = 0
    with open(seed_file_path, 'r') as f:
        for line in f:
            # Basic cleaning
            url = line.strip()
            if url and url.startswith('http'):
                if url not in existing_urls:
                    new_urls.append({'url': url})
                    existing_urls.add(url)
                else:
                    duplicate_count += 1

    if new_urls:
        logger.info(f"Found {len(new_urls)} new URLs to add ({duplicate_count} duplicates skipped).")
        # Append to existing table (synchronous write)
        delta_manager.write('seed_urls', new_urls, mode='append', async_write=False)
        logger.info(f"Successfully added {len(new_urls)} seed URLs. Total: {len(existing_urls)}")
    else:
        if duplicate_count > 0:
            logger.info(f"All {duplicate_count} URLs from CSV already exist in seed_urls table.")
        else:
            logger.warning("No valid seed URLs found in the seed file.")

if __name__ == "__main__":
    load_seeds()
