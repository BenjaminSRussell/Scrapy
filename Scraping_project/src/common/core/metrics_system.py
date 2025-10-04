"""
Unified Metrics System - Consolidates all performance tracking

Tracks:
- Real-time performance (items/sec, CPU, memory)
- Failed URLs with detailed error tracking
- Stage-specific metrics
- Orchestrator coordination metrics
"""

import json
import logging
import psutil
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class PerformanceSnapshot:
    """Single performance measurement"""
    timestamp: str
    stage: str
    items_processed: int
    items_per_second: float
    items_failed: int
    failure_rate: float
    cpu_percent: float
    memory_mb: float
    memory_percent: float
    thread_count: int
    elapsed_seconds: float
    queue_size: int = 0
    active_workers: int = 0


@dataclass
class FailedURL:
    """Track failed URL with details"""
    url: str
    url_hash: str
    stage: str
    error_type: str
    error_message: str
    status_code: int
    retry_count: int
    timestamp: str
    traceback: Optional[str] = None


class MetricsCollector:
    """Collect and aggregate metrics across pipeline"""
    
    def __init__(self):
        self.metrics: dict[str, list[PerformanceSnapshot]] = defaultdict(list)
        self.failed_urls: list[FailedURL] = []
        self.stage_counters: dict[str, dict[str, int]] = defaultdict(lambda: {
            "processed": 0,
            "failed": 0,
            "retried": 0
        })
        self._lock = threading.Lock()
    
    def record_snapshot(self, snapshot: PerformanceSnapshot):
        """Record performance snapshot"""
        with self._lock:
            self.metrics[snapshot.stage].append(snapshot)
    
    def record_failure(self, failed: FailedURL):
        """Record failed URL"""
        with self._lock:
            self.failed_urls.append(failed)
            self.stage_counters[failed.stage]["failed"] += 1
    
    def increment_processed(self, stage: str, count: int = 1):
        """Increment processed counter"""
        with self._lock:
            self.stage_counters[stage]["processed"] += count
    
    def get_summary(self, stage: Optional[str] = None) -> dict:
        """Get summary statistics"""
        with self._lock:
            if stage:
                return {
                    "stage": stage,
                    "snapshots": len(self.metrics.get(stage, [])),
                    "counters": dict(self.stage_counters.get(stage, {})),
                    "failures": len([f for f in self.failed_urls if f.stage == stage])
                }
            
            return {
                "total_snapshots": sum(len(m) for m in self.metrics.values()),
                "total_failures": len(self.failed_urls),
                "stages": {s: dict(c) for s, c in self.stage_counters.items()}
            }


class PerformanceTracker:
    """Real-time performance tracking for a pipeline stage"""
    
    def __init__(
        self,
        stage: str,
        collector: Optional[MetricsCollector] = None,
        log_interval: int = 10
    ):
        self.stage = stage
        self.collector = collector or MetricsCollector()
        self.log_interval = log_interval
        self.output_file = Path(f"data/logs/performance_{stage}.jsonl")
        
        self.start_time = time.time()
        self.items_processed = 0
        self.items_failed = 0
        self.last_items_processed = 0
        self.last_log_time = self.start_time
        
        self.queue_size = 0
        self.active_workers = 0
        
        self.running = False
        self.monitor_thread: Optional[threading.Thread] = None
        self.process = psutil.Process()
        
        self.output_file.parent.mkdir(parents=True, exist_ok=True)
    
    def start(self):
        """Start background monitoring"""
        if self.running:
            return
        
        self.running = True
        self.monitor_thread = threading.Thread(target=self._monitoring_loop, daemon=True)
        self.monitor_thread.start()
        logger.info(f"Performance monitoring started: {self.stage}")
    
    def stop(self):
        """Stop monitoring"""
        self.running = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=2)
        self._log_snapshot()
    
    def increment(self, count: int = 1):
        """Increment processed counter"""
        self.items_processed += count
        self.collector.increment_processed(self.stage, count)
    
    def record_failure(self, url: str, url_hash: str, error_type: str, 
                      error_message: str, status_code: int = 0):
        """Record failed URL"""
        self.items_failed += 1
        failed = FailedURL(
            url=url, url_hash=url_hash, stage=self.stage,
            error_type=error_type, error_message=error_message,
            status_code=status_code, retry_count=0,
            timestamp=datetime.now().isoformat()
        )
        self.collector.record_failure(failed)
    
    def _monitoring_loop(self):
        """Background loop"""
        while self.running:
            try:
                time.sleep(self.log_interval)
                if self.running:
                    self._log_snapshot()
            except Exception as e:
                logger.error(f"Monitoring error: {e}")
    
    def _log_snapshot(self):
        """Log performance snapshot"""
        try:
            current_time = time.time()
            elapsed = current_time - self.start_time
            
            items_delta = self.items_processed - self.last_items_processed
            time_delta = current_time - self.last_log_time
            items_per_sec = items_delta / time_delta if time_delta > 0 else 0
            
            total = self.items_processed + self.items_failed
            failure_rate = (self.items_failed / total * 100) if total > 0 else 0
            
            cpu = self.process.cpu_percent(interval=0.1)
            mem_mb = self.process.memory_info().rss / (1024 * 1024)
            mem_pct = self.process.memory_percent()
            
            snapshot = PerformanceSnapshot(
                timestamp=datetime.now().isoformat(),
                stage=self.stage,
                items_processed=self.items_processed,
                items_per_second=round(items_per_sec, 2),
                items_failed=self.items_failed,
                failure_rate=round(failure_rate, 2),
                cpu_percent=round(cpu, 1),
                memory_mb=round(mem_mb, 1),
                memory_percent=round(mem_pct, 1),
                thread_count=self.process.num_threads(),
                elapsed_seconds=round(elapsed, 1),
                queue_size=self.queue_size,
                active_workers=self.active_workers
            )
            
            self.collector.record_snapshot(snapshot)
            
            with open(self.output_file, "a") as f:
                f.write(json.dumps(asdict(snapshot)) + "
")
            
            logger.info(
                f"[{self.stage}] {self.items_processed:,} items | "
                f"{items_per_sec:.1f}/s | Fail: {failure_rate:.1f}% | "
                f"CPU: {cpu:.0f}% | Mem: {mem_mb:.0f}MB"
            )
            
            self.last_items_processed = self.items_processed
            self.last_log_time = current_time
            
        except Exception as e:
            logger.error(f"Snapshot error: {e}")


_global_collector = MetricsCollector()

def get_global_collector() -> MetricsCollector:
    return _global_collector
