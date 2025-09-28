"""
Data Refresh and Recheck System

Handles intelligent rechecking and updating of scraped data with:
- Differential updates (only recheck changed/failed URLs)
- Priority-based refresh (critical content first)
- Data validation and comparison
- Incremental file generation
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Any
from dataclasses import dataclass, asdict

from common.request_infrastructure import SmartRequestHandler, AdaptiveRequestConfig
from common.schemas import DiscoveryItem, ValidationResult, EnrichmentItem

logger = logging.getLogger(__name__)


@dataclass
class RefreshConfig:
    """Configuration for data refresh operations"""
    max_concurrent: int = 20
    priority_domains: List[str] = None
    refresh_interval_hours: int = 24
    force_refresh_failed: bool = True
    update_success_data: bool = False
    create_incremental_files: bool = True
    backup_existing: bool = True


@dataclass
class RefreshResult:
    """Result of a data refresh operation"""
    url: str
    old_content_length: Optional[int]
    new_content_length: Optional[int]
    changed: bool
    success: bool
    error_message: Optional[str]
    refresh_timestamp: str
    processing_time: float


class DataRefreshManager:
    """Manages intelligent refresh and recheck of scraped data"""

    def __init__(self, config: RefreshConfig = None):
        self.config = config or RefreshConfig()
        self.request_config = AdaptiveRequestConfig()
        self.request_handler = None

        # File paths for different stages
        self.discovery_file = Path("data/processed/stage01/discovered_urls.jsonl")
        self.validation_file = Path("data/processed/stage02/validated_urls.jsonl")
        self.enrichment_file = Path("data/processed/stage03/enriched_content.jsonl")

        # Analytics and tracking
        self.refresh_history = Path("data/analytics/refresh_history.json")
        self.refresh_history.parent.mkdir(parents=True, exist_ok=True)

    async def __aenter__(self):
        self.request_handler = SmartRequestHandler(self.request_config)
        await self.request_handler.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.request_handler:
            await self.request_handler.__aexit__(exc_type, exc_val, exc_tb)

    def _get_content_length(self, content: str) -> int:
        """Get content length for simple change detection"""
        return len(content) if content else 0

    def _load_existing_data(self, file_path: Path) -> Dict[str, Any]:
        """Load existing data from file"""
        if not file_path.exists():
            return {}

        data = {}
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        item = json.loads(line)
                        url = item.get('url') or item.get('discovered_url')
                        if url:
                            data[url] = item
        except Exception as e:
            logger.error(f"Error loading {file_path}: {e}")

        return data

    def _backup_file(self, file_path: Path):
        """Create backup of existing file"""
        if not file_path.exists():
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = file_path.with_suffix(f".{timestamp}.backup")

        try:
            backup_path.write_text(file_path.read_text())
            logger.info(f"Created backup: {backup_path}")
        except Exception as e:
            logger.error(f"Failed to create backup: {e}")

    def _get_refresh_priorities(self, existing_data: Dict[str, Any]) -> List[Tuple[str, int]]:
        """Determine refresh priorities based on various factors"""
        priorities = []

        for url, data in existing_data.items():
            priority = 0

            # Priority factors
            domain = url.split('/')[2] if '//' in url else url

            # High priority for configured domains
            if self.config.priority_domains and any(d in domain for d in self.config.priority_domains):
                priority += 100

            # High priority for previously failed requests
            if not data.get('is_valid', True) or data.get('error_message'):
                priority += 50

            # Medium priority for old data
            last_updated = data.get('validated_at') or data.get('enriched_at') or data.get('first_seen')
            if last_updated:
                try:
                    update_time = datetime.fromisoformat(last_updated.replace('Z', '+00:00'))
                    hours_old = (datetime.now() - update_time.replace(tzinfo=None)).total_seconds() / 3600
                    if hours_old > self.config.refresh_interval_hours:
                        priority += max(0, min(25, int(hours_old / 24)))  # Up to 25 points for age
                except:
                    priority += 10  # Unknown age gets medium priority

            # Lower priority for successful, recent data
            if data.get('is_valid') and data.get('status_code', 0) == 200:
                priority -= 10

            priorities.append((url, priority))

        # Sort by priority (high to low)
        return sorted(priorities, key=lambda x: x[1], reverse=True)

    async def refresh_validation_data(self, force_all: bool = False) -> Dict[str, Any]:
        """Refresh validation data with intelligent prioritization"""
        logger.info("Starting validation data refresh...")

        if self.config.backup_existing:
            self._backup_file(self.validation_file)

        existing_data = self._load_existing_data(self.validation_file)
        logger.info(f"Loaded {len(existing_data)} existing validation records")

        # Determine what needs refreshing
        if force_all:
            urls_to_refresh = list(existing_data.keys())
        else:
            priorities = self._get_refresh_priorities(existing_data)
            # Refresh high priority items (priority > 0) or failed items
            urls_to_refresh = [url for url, priority in priorities if priority > 0 or not existing_data[url].get('is_valid', True)]

        logger.info(f"Refreshing {len(urls_to_refresh)} URLs out of {len(existing_data)}")

        if not urls_to_refresh:
            return {"message": "No URLs need refreshing", "refreshed": 0}

        # Process in batches
        results = []
        semaphore = asyncio.Semaphore(self.config.max_concurrent)

        async def refresh_single_validation(url: str) -> RefreshResult:
            async with semaphore:
                start_time = asyncio.get_event_loop().time()
                old_data = existing_data.get(url, {})
                old_content_length = old_data.get('content_length', 0)

                try:
                    # Fetch with smart request handler
                    request_result = await self.request_handler.fetch_with_learning(url)

                    if request_result.success:
                        new_content_length = self._get_content_length(request_result.content or "")
                        changed = old_content_length != new_content_length

                        # Create updated validation result
                        validation_result = ValidationResult(
                            url=url,
                            status_code=request_result.final_status_code,
                            content_type=request_result.content_type or '',
                            content_length=request_result.content_length,
                            response_time=request_result.total_time,
                            is_valid=200 <= (request_result.final_status_code or 0) < 400,
                            error_message=None,
                            validated_at=datetime.now().isoformat(),
                            learned_optimizations=request_result.learned_optimizations
                        )

                        # Update existing data
                        existing_data[url] = asdict(validation_result)

                        return RefreshResult(
                            url=url,
                            old_content_length=old_content_length,
                            new_content_length=new_content_length,
                            changed=changed,
                            success=True,
                            error_message=None,
                            refresh_timestamp=datetime.now().isoformat(),
                            processing_time=asyncio.get_event_loop().time() - start_time
                        )

                    else:
                        # Update with failure information
                        old_data.update({
                            'is_valid': False,
                            'error_message': f"Request failed: {request_result.attempts[-1].error_message if request_result.attempts else 'Unknown error'}",
                            'validated_at': datetime.now().isoformat(),
                            'status_code': 0
                        })
                        existing_data[url] = old_data

                        return RefreshResult(
                            url=url,
                            old_content_length=old_content_length,
                            new_content_length=None,
                            changed=False,
                            success=False,
                            error_message=f"Request failed: {request_result.attempts[-1].error_message if request_result.attempts else 'Unknown error'}",
                            refresh_timestamp=datetime.now().isoformat(),
                            processing_time=asyncio.get_event_loop().time() - start_time
                        )

                except Exception as e:
                    logger.error(f"Error refreshing {url}: {e}")
                    return RefreshResult(
                        url=url,
                        old_content_length=old_content_length,
                        new_content_length=None,
                        changed=False,
                        success=False,
                        error_message=str(e),
                        refresh_timestamp=datetime.now().isoformat(),
                        processing_time=asyncio.get_event_loop().time() - start_time
                    )

        # Execute refreshes concurrently
        tasks = [refresh_single_validation(url) for url in urls_to_refresh]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Filter out exceptions and process results
        valid_results = [r for r in results if isinstance(r, RefreshResult)]
        successful_refreshes = [r for r in valid_results if r.success]
        failed_refreshes = [r for r in valid_results if not r.success]
        changed_items = [r for r in valid_results if r.changed]

        logger.info(f"Refresh complete: {len(successful_refreshes)} successful, {len(failed_refreshes)} failed, {len(changed_items)} changed")

        # Write updated data back to file
        self._write_updated_data(self.validation_file, existing_data)

        # Create incremental update file if configured
        if self.config.create_incremental_files:
            self._create_incremental_file(self.validation_file, changed_items, "validation")

        # Save refresh history
        self._save_refresh_history("validation", valid_results)

        return {
            "total_processed": len(valid_results),
            "successful": len(successful_refreshes),
            "failed": len(failed_refreshes),
            "changed": len(changed_items),
            "processing_time": sum(r.processing_time for r in valid_results),
            "performance_summary": self.request_handler.get_performance_summary()
        }

    def _write_updated_data(self, file_path: Path, data: Dict[str, Any]):
        """Write updated data back to file"""
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                for item in data.values():
                    f.write(json.dumps(item, ensure_ascii=False) + '\n')
            logger.info(f"Updated {len(data)} records in {file_path}")
        except Exception as e:
            logger.error(f"Error writing updated data to {file_path}: {e}")

    def _create_incremental_file(self, base_file: Path, changed_items: List[RefreshResult], stage_name: str):
        """Create incremental update file with only changed items"""
        if not changed_items:
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        incremental_file = base_file.with_name(f"{base_file.stem}_incremental_{timestamp}.jsonl")

        try:
            existing_data = self._load_existing_data(base_file)
            with open(incremental_file, 'w', encoding='utf-8') as f:
                for result in changed_items:
                    if result.url in existing_data:
                        f.write(json.dumps(existing_data[result.url], ensure_ascii=False) + '\n')

            logger.info(f"Created incremental {stage_name} file: {incremental_file} with {len(changed_items)} changes")

        except Exception as e:
            logger.error(f"Error creating incremental file: {e}")

    def _save_refresh_history(self, stage: str, results: List[RefreshResult]):
        """Save refresh history for analytics"""
        try:
            history = []
            if self.refresh_history.exists():
                with open(self.refresh_history, 'r') as f:
                    history = json.load(f)

            history.append({
                'stage': stage,
                'timestamp': datetime.now().isoformat(),
                'total_processed': len(results),
                'successful': len([r for r in results if r.success]),
                'failed': len([r for r in results if not r.success]),
                'changed': len([r for r in results if r.changed]),
                'avg_processing_time': sum(r.processing_time for r in results) / max(1, len(results))
            })

            # Keep only last 100 refresh operations
            history = history[-100:]

            with open(self.refresh_history, 'w') as f:
                json.dump(history, f, indent=2)

        except Exception as e:
            logger.error(f"Error saving refresh history: {e}")

    async def full_data_refresh(self) -> Dict[str, Any]:
        """Perform full refresh of all data stages"""
        logger.info("Starting full data refresh...")

        results = {}

        # Refresh validation data first
        validation_result = await self.refresh_validation_data(force_all=False)
        results['validation'] = validation_result

        # TODO: Add enrichment data refresh if needed
        # enrichment_result = await self.refresh_enrichment_data()
        # results['enrichment'] = enrichment_result

        logger.info("Full data refresh completed")
        return results

    def get_refresh_status(self) -> Dict[str, Any]:
        """Get current refresh status and recommendations"""
        existing_validation = self._load_existing_data(self.validation_file)

        # Analyze what needs refreshing
        priorities = self._get_refresh_priorities(existing_validation)
        high_priority = [url for url, priority in priorities if priority > 50]
        failed_items = [url for url, data in existing_validation.items() if not data.get('is_valid', True)]

        # Check last refresh
        last_refresh = None
        if self.refresh_history.exists():
            try:
                with open(self.refresh_history, 'r') as f:
                    history = json.load(f)
                if history:
                    last_refresh = history[-1]['timestamp']
            except:
                pass

        return {
            'total_urls': len(existing_validation),
            'high_priority_count': len(high_priority),
            'failed_count': len(failed_items),
            'last_refresh': last_refresh,
            'recommendations': {
                'should_refresh': len(high_priority) > 0 or len(failed_items) > 10,
                'high_priority_domains': list(set([url.split('/')[2] for url in high_priority[:10] if '//' in url])),
                'estimated_time_minutes': max(1, (len(high_priority) + len(failed_items)) / 10)
            }
        }