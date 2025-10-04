"""
Performance Metrics and Time Series Logging

Tracks and logs pipeline performance metrics every 10 seconds for monitoring and analysis.
"""

import json
import logging
import psutil
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class PerformanceSnapshot:
    """Single performance measurement snapshot"""
    timestamp: str
    stage: str
    items_processed: int
    items_per_second: float
    cpu_percent: float
    memory_mb: float
    memory_percent: float
    thread_count: int
    elapsed_seconds: float


class PerformanceMonitor:
    """Monitors and logs performance metrics in real-time"""

    def __init__(
        self,
        stage: str,
        output_file: Optional[Path] = None,
        log_interval: int = 10
    ):
        self.stage = stage
        self.output_file = output_file or Path(f"data/logs/performance_{stage}.jsonl")
        self.log_interval = log_interval  # seconds

        self.start_time = time.time()
        self.items_processed = 0
        self.last_items_processed = 0
        self.last_log_time = self.start_time

        self.running = False
        self.monitor_thread: Optional[threading.Thread] = None
        self.process = psutil.Process()

        # Ensure output directory exists
        self.output_file.parent.mkdir(parents=True, exist_ok=True)

        logger.info(f"Performance monitor initialized for {stage} - logging every {log_interval}s to {self.output_file}")

    def start(self):
        """Start background monitoring thread"""
        if self.running:
            return

        self.running = True
        self.monitor_thread = threading.Thread(target=self._monitoring_loop, daemon=True)
        self.monitor_thread.start()
        logger.info(f"Performance monitoring started for {self.stage}")

    def stop(self):
        """Stop monitoring and log final metrics"""
        if not self.running:
            return

        self.running = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=2)

        # Log final snapshot
        self._log_snapshot()
        logger.info(f"Performance monitoring stopped for {self.stage}")

    def increment(self, count: int = 1):
        """Increment items processed counter"""
        self.items_processed += count

    def _monitoring_loop(self):
        """Background loop that logs metrics every N seconds"""
        while self.running:
            try:
                time.sleep(self.log_interval)
                if self.running:  # Check again after sleep
                    self._log_snapshot()
            except Exception as e:
                logger.error(f"Error in performance monitoring loop: {e}")

    def _log_snapshot(self):
        """Capture and log current performance snapshot"""
        try:
            current_time = time.time()
            elapsed = current_time - self.start_time

            # Calculate items/sec over the last interval
            items_delta = self.items_processed - self.last_items_processed
            time_delta = current_time - self.last_log_time
            items_per_sec = items_delta / time_delta if time_delta > 0 else 0

            # Get system metrics
            cpu_percent = self.process.cpu_percent(interval=0.1)
            memory_info = self.process.memory_info()
            memory_mb = memory_info.rss / (1024 * 1024)
            memory_percent = self.process.memory_percent()
            thread_count = self.process.num_threads()

            # Create snapshot
            snapshot = PerformanceSnapshot(
                timestamp=datetime.now().isoformat(),
                stage=self.stage,
                items_processed=self.items_processed,
                items_per_second=round(items_per_sec, 2),
                cpu_percent=round(cpu_percent, 1),
                memory_mb=round(memory_mb, 1),
                memory_percent=round(memory_percent, 1),
                thread_count=thread_count,
                elapsed_seconds=round(elapsed, 1)
            )

            # Write to file
            with open(self.output_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(asdict(snapshot)) + '\n')

            # Log summary
            logger.info(
                f"[{self.stage}] Performance: {self.items_processed} items "
                f"({items_per_sec:.1f}/s) | CPU: {cpu_percent:.1f}% | "
                f"Memory: {memory_mb:.0f}MB ({memory_percent:.1f}%) | "
                f"Threads: {thread_count}"
            )

            # Update last values
            self.last_items_processed = self.items_processed
            self.last_log_time = current_time

        except Exception as e:
            logger.error(f"Error logging performance snapshot: {e}")

    def get_summary(self) -> dict:
        """Get summary statistics"""
        elapsed = time.time() - self.start_time
        avg_rate = self.items_processed / elapsed if elapsed > 0 else 0

        return {
            'stage': self.stage,
            'total_items': self.items_processed,
            'elapsed_seconds': round(elapsed, 1),
            'average_rate': round(avg_rate, 2),
            'cpu_percent': round(self.process.cpu_percent(), 1),
            'memory_mb': round(self.process.memory_info().rss / (1024 * 1024), 1)
        }


def load_performance_metrics(metrics_file: Path) -> list[PerformanceSnapshot]:
    """Load performance metrics from JSONL file"""
    metrics = []

    if not metrics_file.exists():
        return metrics

    with open(metrics_file, 'r', encoding='utf-8') as f:
        for line in f:
            try:
                data = json.loads(line)
                metrics.append(PerformanceSnapshot(**data))
            except Exception as e:
                logger.warning(f"Skipping invalid metric line: {e}")

    return metrics


def plot_performance_metrics(metrics_file: Path, output_image: Optional[Path] = None):
    """Generate performance visualization (requires matplotlib)"""
    try:
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        from datetime import datetime as dt
    except ImportError:
        logger.error("matplotlib not installed. Run: pip install matplotlib")
        return

    metrics = load_performance_metrics(metrics_file)
    if not metrics:
        logger.warning(f"No metrics found in {metrics_file}")
        return

    # Extract data
    timestamps = [dt.fromisoformat(m.timestamp) for m in metrics]
    items_per_sec = [m.items_per_second for m in metrics]
    cpu_percent = [m.cpu_percent for m in metrics]
    memory_mb = [m.memory_mb for m in metrics]

    # Create figure with subplots
    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(12, 10), sharex=True)

    # Plot throughput
    ax1.plot(timestamps, items_per_sec, 'b-', linewidth=2)
    ax1.set_ylabel('Items/Second', fontsize=12)
    ax1.set_title(f'Performance Metrics: {metrics[0].stage.upper()}', fontsize=14, fontweight='bold')
    ax1.grid(True, alpha=0.3)

    # Plot CPU
    ax2.plot(timestamps, cpu_percent, 'r-', linewidth=2)
    ax2.set_ylabel('CPU %', fontsize=12)
    ax2.grid(True, alpha=0.3)

    # Plot Memory
    ax3.plot(timestamps, memory_mb, 'g-', linewidth=2)
    ax3.set_ylabel('Memory (MB)', fontsize=12)
    ax3.set_xlabel('Time', fontsize=12)
    ax3.grid(True, alpha=0.3)

    # Format x-axis
    ax3.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
    plt.setp(ax3.xaxis.get_majorticklabels(), rotation=45, ha='right')

    plt.tight_layout()

    if output_image:
        plt.savefig(output_image, dpi=150, bbox_inches='tight')
        logger.info(f"Performance plot saved to {output_image}")
    else:
        plt.show()


if __name__ == '__main__':
    # Example usage and testing
    import sys

    if len(sys.argv) > 1:
        metrics_file = Path(sys.argv[1])
        output_image = Path(sys.argv[2]) if len(sys.argv) > 2 else None
        plot_performance_metrics(metrics_file, output_image)
    else:
        print("Usage: python performance_metrics.py <metrics_file.jsonl> [output_image.png]")
