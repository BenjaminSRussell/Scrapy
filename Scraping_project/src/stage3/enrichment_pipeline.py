import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from itemadapter import ItemAdapter

from .storage import create_storage_writer

logger = logging.getLogger(__name__)


class Stage3Pipeline:
    """Pipeline for Stage 3 Enrichment with pluggable storage backends."""

    DEFAULT_OUTPUT = Path("data/processed/stage03/enriched_content.jsonl")

    def __init__(
        self,
        output_file: Optional[str] = None,
        storage_config: Optional[Dict[str, Any]] = None,
        storage_backend: Optional[str] = None,
        storage_options: Optional[Dict[str, Any]] = None,
        rotation_config: Optional[Dict[str, Any]] = None,
        compression_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.output_path = Path(output_file) if output_file else self.DEFAULT_OUTPUT
        base_config: Dict[str, Any] = dict(storage_config or {})

        if storage_backend:
            base_config["backend"] = storage_backend
        if storage_options:
            merged_options = dict(base_config.get("options") or {})
            merged_options.update(storage_options)
            base_config["options"] = merged_options
        if rotation_config is not None:
            base_config["rotation"] = rotation_config
        if compression_config is not None:
            base_config["compression"] = compression_config

        self.storage_config = base_config
        self.active_backend = (base_config.get("backend") or "jsonl").lower()
        self.writer = None
        self.item_count = 0

    @classmethod
    def from_crawler(cls, crawler):
        """Create pipeline instance from crawler settings."""
        settings = crawler.settings
        output_file = settings.get("STAGE3_OUTPUT_FILE")
        storage_config = settings.getdict("STAGE3_STORAGE", default={})
        storage_backend = settings.get("STAGE3_STORAGE_BACKEND")
        storage_options = settings.getdict("STAGE3_STORAGE_OPTIONS", default={})
        rotation_config = settings.getdict("STAGE3_STORAGE_ROTATION", default={})
        compression_config = settings.getdict("STAGE3_STORAGE_COMPRESSION", default={})
        return cls(
            output_file=output_file,
            storage_config=storage_config,
            storage_backend=storage_backend,
            storage_options=storage_options,
            rotation_config=rotation_config or None,
            compression_config=compression_config or None,
        )

    def _build_writer(self):
        config = dict(self.storage_config)
        backend = (config.get("backend") or "jsonl").lower()
        self.active_backend = backend
        options = dict(config.get("options") or {})
        rotation = config.get("rotation")
        compression = config.get("compression")

        default_path = self.output_path
        if backend != "s3" and "path" not in options:
            options["path"] = str(default_path)

        return create_storage_writer(
            backend=backend,
            options=options,
            rotation_config=rotation,
            compression_config=compression,
            default_path=default_path,
        )

    def open_spider(self, spider):
        """Initialize pipeline when spider opens."""
        try:
            self.writer = self._build_writer()
            self.writer.open()
        except Exception as exc:
            logger.error("[Stage3Pipeline] Failed to initialize storage backend", exc_info=True)
            raise

        self.item_count = 0
        logger.info(
            "[Stage3Pipeline] Using %s backend → %s",
            self.active_backend.upper(),
            self.writer.describe_destination(),
        )

    def close_spider(self, spider):
        """Clean up when spider closes."""
        if self.writer:
            try:
                self.writer.close()
            finally:
                destination = self.writer.describe_destination()
                logger.info(
                    "[Stage3Pipeline] Processed %s enriched items → %s",
                    f"{self.item_count:,}",
                    destination,
                )
                self.writer = None

    def process_item(self, item, spider):
        """Process each enriched content item."""
        adapter = ItemAdapter(item)

        enrichment_data = {
            "url": adapter.get("url"),
            "url_hash": adapter.get("url_hash"),
            "title": adapter.get("title", ""),
            "text_content": adapter.get("text_content", ""),
            "word_count": adapter.get("word_count", 0),
            "entities": adapter.get("entities", []),
            "keywords": adapter.get("keywords", []),
            "content_tags": adapter.get("content_tags", []),
            "has_pdf_links": adapter.get("has_pdf_links", False),
            "has_audio_links": adapter.get("has_audio_links", False),
            "status_code": adapter.get("status_code"),
            "content_type": adapter.get("content_type"),
            "enriched_at": adapter.get("enriched_at"),
            "processed_at": datetime.now().isoformat(),
        }

        if not self.writer:
            raise RuntimeError("Stage3Pipeline writer is not initialized")

        try:
            self.writer.write_item(enrichment_data)
            self.item_count += 1

            if self.item_count % 100 == 0:
                logger.info("[Stage3Pipeline] Processed %s enriched items", f"{self.item_count:,}")
        except Exception as exc:
            logger.error(f"[Stage3Pipeline] Error writing enriched item: {exc}")

        return item

