
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
    Loads seed URLs from a CSV file, cleans them, and saves them to a Delta Lake table.
    """
    seed_file_path = Path(__file__).parent.parent / "data" / "raw" / "uconn_urls.csv"
    if not seed_file_path.exists():
        logger.error(f"Seed file not found at: {seed_file_path}")
        return

    delta_manager = get_delta_manager()
    
    urls = []
    with open(seed_file_path, 'r') as f:
        for line in f:
            # Basic cleaning
            url = line.strip()
            if url and url.startswith('http'):
                urls.append({'url': url})

    if urls:
        logger.info(f"Found {len(urls)} seed URLs to load.")
        # Write to a new 'seed_urls' table, overwriting if it exists
        delta_manager.write('seed_urls', urls, mode='overwrite')
        logger.info("Successfully loaded seed URLs into Delta Lake table 'seed_urls'.")
    else:
        logger.warning("No valid seed URLs found in the seed file.")

if __name__ == "__main__":
    load_seeds()
