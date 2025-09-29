import logging
from pathlib import Path


def setup_logging(log_level: str = 'INFO', log_dir: Path = None):
    """Simple logging setup"""
    level = getattr(logging, log_level.upper(), logging.INFO)

    format_str = '%(asctime)s [%(levelname)s] %(message)s'
    logging.basicConfig(level=level, format=format_str, datefmt='%H:%M:%S')

    if log_dir:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_dir / 'pipeline.log')
        file_handler.setFormatter(logging.Formatter(format_str))
        logging.getLogger().addHandler(file_handler)

    # Reduce noise from external libraries
    for lib in ['scrapy', 'twisted', 'aiohttp']:
        logging.getLogger(lib).setLevel(logging.WARNING)