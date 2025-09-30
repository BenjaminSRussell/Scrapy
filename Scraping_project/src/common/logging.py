import logging
from pathlib import Path


def setup_logging(log_level: str = 'INFO', log_dir: Path = None):
    """Set up logging because apparently we need to know what's happening."""
    level = getattr(logging, log_level.upper(), logging.INFO)

    format_str = '%(asctime)s [%(levelname)s] %(message)s'
    # Clear any existing handlers because logging is a global mess
    logging.getLogger().handlers.clear()
    logging.basicConfig(level=level, format=format_str, datefmt='%H:%M:%S', force=True)

    if log_dir:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_dir / 'pipeline.log')
        file_handler.setLevel(level)  # Actually set the level on the handler, genius
        file_handler.setFormatter(logging.Formatter(format_str))
        logging.getLogger().addHandler(file_handler)

    # Tell the noisy libraries to shut up
    for lib in ['scrapy', 'twisted', 'aiohttp']:
        logging.getLogger(lib).setLevel(logging.WARNING)