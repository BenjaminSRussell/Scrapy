"""
Checkpoint middleware for Scrapy spiders.

Provides checkpoint/resume functionality for all pipeline stages
without requiring major modifications to existing spider code.
"""

import logging
from pathlib import Path
from typing import Optional

from scrapy import signals
from scrapy.http import Request, Response
from scrapy.exceptions import NotConfigured

from src.common.enhanced_checkpoints import UnifiedCheckpointManager, CheckpointStatus

logger = logging.getLogger(__name__)


class CheckpointMiddleware:
    """
    Scrapy middleware that adds checkpoint/resume support.

    Automatically saves progress and allows resuming from last checkpoint.
    """

    def __init__(self, checkpoint_dir: str, stage_name: str, enabled: bool = True):
        if not enabled:
            raise NotConfigured("Checkpoint middleware is disabled")

        self.checkpoint_dir = Path(checkpoint_dir)
        self.stage_name = stage_name
        self.checkpoint_manager = UnifiedCheckpointManager(self.checkpoint_dir)
        self.checkpoint = self.checkpoint_manager.get_checkpoint(stage_name)

        self.total_urls = 0
        self.processed_urls = 0
        self.successful_urls = 0
        self.failed_urls = 0

    @classmethod
    def from_crawler(cls, crawler):
        """Create middleware from crawler"""
        settings = crawler.settings

        # Get checkpoint settings
        checkpoint_dir = settings.get('CHECKPOINT_DIR', 'data/checkpoints')
        stage_name = settings.get('CHECKPOINT_STAGE_NAME', 'unknown')
        enabled = settings.getbool('CHECKPOINT_ENABLED', True)

        # Create middleware
        middleware = cls(
            checkpoint_dir=checkpoint_dir,
            stage_name=stage_name,
            enabled=enabled
        )

        # Connect signals
        crawler.signals.connect(middleware.spider_opened, signal=signals.spider_opened)
        crawler.signals.connect(middleware.spider_closed, signal=signals.spider_closed)
        crawler.signals.connect(middleware.item_scraped, signal=signals.item_scraped)
        crawler.signals.connect(middleware.item_dropped, signal=signals.item_dropped)
        crawler.signals.connect(middleware.item_error, signal=signals.item_error)

        return middleware

    def spider_opened(self, spider):
        """Called when spider opens"""
        # Check if we should resume
        resume_point = self.checkpoint.get_resume_point()

        if resume_point['status'] in ['running', 'recovering']:
            logger.warning(
                f"Detected incomplete checkpoint for {self.stage_name}. "
                f"Resuming from {resume_point['processed_items']} items"
            )
            self.checkpoint.resume()
        elif resume_point['status'] == 'completed':
            logger.info(f"Checkpoint {self.stage_name} already completed. Use --reset to start fresh")
            # Optionally could skip spider entirely
        else:
            # Start fresh
            self.checkpoint.start(self.stage_name, total_items=0)

        logger.info(f"Checkpoint middleware enabled for {self.stage_name}")

    def spider_closed(self, spider, reason):
        """Called when spider closes"""
        if reason == 'finished':
            self.checkpoint.complete()
            logger.info(f"Checkpoint {self.stage_name} completed successfully")
        elif reason in ['shutdown', 'cancelled']:
            self.checkpoint.pause()
            logger.info(f"Checkpoint {self.stage_name} paused (can resume)")
        else:
            self.checkpoint.fail(f"Spider closed with reason: {reason}")
            logger.error(f"Checkpoint {self.stage_name} failed: {reason}")

        # Print final report
        report = self.checkpoint.get_progress_report()
        logger.info(
            f"Final stats - Processed: {report['processed']}, "
            f"Success: {report['successful']}, Failed: {report['failed']}"
        )

    def process_spider_output(self, response, result, spider):
        """Process spider output (requests and items)"""
        for item in result:
            # Track requests for checkpoint
            if isinstance(item, Request):
                self.total_urls += 1

                # Update total in checkpoint
                if self.checkpoint.state.progress.total_items == 0:
                    self.checkpoint.state.progress.total_items = 1

            yield item

    def item_scraped(self, item, response, spider):
        """Called when item is successfully scraped"""
        self.processed_urls += 1
        self.successful_urls += 1

        # Update checkpoint
        self.checkpoint.update_progress(
            processed=1,
            successful=1,
            last_item=str(response.url),
            index=self.processed_urls
        )

        # Log progress periodically
        if self.processed_urls % 100 == 0:
            report = self.checkpoint.get_progress_report()
            logger.info(
                f"Progress: {report['progress_pct']:.1f}% "
                f"({report['processed']}/{report['total']}) | "
                f"Success: {report['success_rate']:.1f}% | "
                f"Throughput: {report['throughput']:.1f} items/sec"
            )

    def item_dropped(self, item, response, exception, spider):
        """Called when item is dropped"""
        self.processed_urls += 1

        self.checkpoint.update_progress(
            processed=1,
            skipped=1,
            last_item=str(response.url),
            index=self.processed_urls
        )

    def item_error(self, item, response, spider, failure):
        """Called when item processing fails"""
        self.processed_urls += 1
        self.failed_urls += 1

        self.checkpoint.update_progress(
            processed=1,
            failed=1,
            last_item=str(response.url),
            index=self.processed_urls
        )


class AsyncCheckpointTracker:
    """
    Checkpoint tracker for async processors (like async enrichment).

    Provides similar functionality to middleware but for non-Scrapy code.
    """

    def __init__(self, checkpoint_dir: Path, stage_name: str):
        self.checkpoint_manager = UnifiedCheckpointManager(checkpoint_dir)
        self.checkpoint = self.checkpoint_manager.get_checkpoint(stage_name)
        self.stage_name = stage_name

    async def __aenter__(self):
        """Async context manager entry"""
        # Check for recovery
        resume_point = self.checkpoint.get_resume_point()

        if resume_point['status'] in ['running', 'recovering']:
            logger.warning(
                f"Detected incomplete checkpoint for {self.stage_name}. "
                f"Resuming from {resume_point['processed_items']} items"
            )
            self.checkpoint.resume()
        elif resume_point['status'] == 'completed':
            logger.info(f"Checkpoint {self.stage_name} already completed")
            self.checkpoint.reset()  # Or could skip
            self.checkpoint.start(self.stage_name, total_items=0)
        else:
            self.checkpoint.start(self.stage_name, total_items=0)

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if exc_type is None:
            self.checkpoint.complete()
            logger.info(f"Checkpoint {self.stage_name} completed successfully")
        else:
            error_msg = f"{exc_type.__name__}: {exc_val}" if exc_val else str(exc_type)
            self.checkpoint.fail(error_msg)
            logger.error(f"Checkpoint {self.stage_name} failed: {error_msg}")

    def start(self, total_items: int, input_file: Optional[str] = None):
        """Start tracking"""
        self.checkpoint.start(self.stage_name, total_items=total_items, input_file=input_file)

    def update(
        self,
        processed: int = 1,
        successful: int = 0,
        failed: int = 0,
        skipped: int = 0,
        last_item: Optional[str] = None,
        index: Optional[int] = None
    ):
        """Update progress"""
        self.checkpoint.update_progress(
            processed=processed,
            successful=successful,
            failed=failed,
            skipped=skipped,
            last_item=last_item,
            index=index
        )

    def complete(self):
        """Mark as complete"""
        self.checkpoint.complete()

    def fail(self, error_message: str):
        """Mark as failed"""
        self.checkpoint.fail(error_message)

    def should_skip(self, index: int) -> bool:
        """Check if should skip item"""
        return self.checkpoint.should_skip(index)

    def get_resume_point(self) -> dict:
        """Get resume information"""
        return self.checkpoint.get_resume_point()

    def print_progress(self):
        """Print progress report"""
        report = self.checkpoint.get_progress_report()
        logger.info(
            f"Progress: {report['progress_pct']:.1f}% "
            f"({report['processed']}/{report['total']}) | "
            f"Success: {report['success_rate']:.1f}% | "
            f"Throughput: {report['throughput']:.1f} items/sec | "
            f"ETA: {report['eta_seconds']/60:.1f} min" if report['eta_seconds'] else ""
        )
