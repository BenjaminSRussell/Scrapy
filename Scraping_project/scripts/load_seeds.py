import hashlib
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.common.delta_lake import get_delta_manager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def _hash_url(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()

def load_seeds():
    seed_file_path = Path(__file__).parent.parent / "data" / "raw" / "uconn_urls.csv"

    try:
        if not seed_file_path.exists():
            logger.error(f"Seed file not found at: {seed_file_path}")
            return
    except Exception as e:
        logger.error(f"Error checking seed file path: {e}")
        return

    delta_manager = get_delta_manager()

    existing_url_hashes = set()
    try:
        try:
            from deltalake import DeltaTable

            table_path = delta_manager.tables.get("seed_urls")
            if table_path and (table_path / "_delta_log").exists():
                dt = DeltaTable(str(table_path))
                pa_table = dt.to_pyarrow_table(columns=["url_hash"])
                existing_url_hashes = set(pa_table["url_hash"].to_pylist())
                logger.info(f"Found {len(existing_url_hashes)} existing seed URL hashes (memory-optimized).")
            else:
                logger.info("No existing seed_urls table found, will create new one.")
        except Exception as proj_error:
            logger.debug(f"Column projection failed, using full read: {proj_error}")
            existing_records = delta_manager.read("seed_urls")
            for record in existing_records:
                if "url_hash" in record:
                    existing_url_hashes.add(record["url_hash"])
                else:
                    existing_url_hashes.add(_hash_url(record["url"]))
            logger.info(f"Found {len(existing_url_hashes)} existing seed URL hashes (fallback mode).")
    except Exception as e:
        logger.info(f"No existing seed_urls table found, will create new one. ({e})")

    new_urls = []
    duplicate_count = 0
    line_count = 0

    try:
        with open(seed_file_path, encoding="utf-8") as f:
            for line in f:
                line_count += 1
                url = line.strip()
                if url and url.startswith("http"):
                    url_hash = _hash_url(url)

                    if url_hash not in existing_url_hashes:
                        new_urls.append(
                            {
                                "url": url,
                                "url_hash": url_hash,
                            }
                        )
                        existing_url_hashes.add(url_hash)
                    else:
                        duplicate_count += 1
    except FileNotFoundError:
        logger.error(f"Seed file not found: {seed_file_path}")
        return
    except Exception as e:
        logger.error(f"Error reading seed file: {e}")
        return

    logger.info(f"Processed {line_count} lines from seed file")

    if new_urls:
        logger.info(f"Found {len(new_urls)} new URLs to add ({duplicate_count} duplicates skipped).")
        try:
            delta_manager.write("seed_urls", new_urls, mode="append", async_write=False)
            logger.info(
                f"✅ Successfully added {len(new_urls)} seed URLs. Total unique URLs: {len(existing_url_hashes)}"
            )
        except Exception as e:
            logger.error(f"Failed to write seed URLs to Delta Lake: {e}")
    else:
        if duplicate_count > 0:
            logger.info(f"All {duplicate_count} URLs from CSV already exist in seed_urls table.")
        else:
            logger.warning("No valid seed URLs found in the seed file.")

if __name__ == "__main__":
    load_seeds()
