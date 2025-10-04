"""
Real-time visual progress display for pipeline execution.
"""

import sys
import time
from collections import deque
from dataclasses import dataclass


@dataclass
class StageMetrics:
    stage_name: str
    processed: int = 0
    total: int = 0
    errors: int = 0
    start_time: float | None = None

    @property
    def progress_pct(self) -> float:
        if self.total == 0:
            return 0.0
        return (self.processed / self.total) * 100

    @property
    def elapsed_seconds(self) -> float:
        if self.start_time is None:
            return 0.0
        return time.time() - self.start_time

    @property
    def rate(self) -> float:
        if self.elapsed_seconds == 0:
            return 0.0
        return self.processed / self.elapsed_seconds


class ProgressDisplay:
    """Real-time ASCII progress display with animations."""

    SPINNER_FRAMES = ['-', '\\', '|', '/']
    BAR_FILLED = '#'
    BAR_EMPTY = '-'
    BAR_WIDTH = 40

    def __init__(self):
        self.stages = {}
        self.spinner_idx = 0
        self.last_update = 0
        self.update_interval = 0.1
        self.rate_samples = deque(maxlen=10)

    def create_stage(self, stage_name: str, total: int = 0):
        """Create a new stage for tracking."""
        self.stages[stage_name] = StageMetrics(
            stage_name=stage_name,
            total=total,
            start_time=time.time()
        )

    def update_stage(self, stage_name: str, processed: int = None, total: int = None, errors: int = None):
        """Update stage metrics."""
        if stage_name not in self.stages:
            self.create_stage(stage_name, total or 0)

        stage = self.stages[stage_name]

        if processed is not None:
            stage.processed = processed
        if total is not None:
            stage.total = total
        if errors is not None:
            stage.errors = errors

        self.rate_samples.append(stage.rate)

    def _render_progress_bar(self, stage: StageMetrics) -> str:
        """Render ASCII progress bar."""
        pct = stage.progress_pct
        filled_width = int((pct / 100) * self.BAR_WIDTH)
        empty_width = self.BAR_WIDTH - filled_width

        bar = f"[{self.BAR_FILLED * filled_width}{self.BAR_EMPTY * empty_width}]"
        return f"{bar} {pct:5.1f}%"

    def _format_rate(self, rate: float) -> str:
        """Format processing rate."""
        if rate < 1:
            return f"{rate:.2f} items/s"
        elif rate < 1000:
            return f"{rate:.0f} items/s"
        else:
            return f"{rate/1000:.1f}k items/s"

    def _format_time(self, seconds: float) -> str:
        """Format elapsed time."""
        if seconds < 60:
            return f"{seconds:.0f}s"
        elif seconds < 3600:
            mins = int(seconds / 60)
            secs = int(seconds % 60)
            return f"{mins}m {secs}s"
        else:
            hours = int(seconds / 3600)
            mins = int((seconds % 3600) / 60)
            return f"{hours}h {mins}m"

    def render(self, force: bool = False):
        """Render the current progress display."""
        now = time.time()

        if not force and (now - self.last_update) < self.update_interval:
            return

        self.last_update = now
        self.spinner_idx = (self.spinner_idx + 1) % len(self.SPINNER_FRAMES)
        spinner = self.SPINNER_FRAMES[self.spinner_idx]

        lines = []
        lines.append("=" * 80)
        lines.append(f"  UConn Web Scraping Pipeline {spinner}")
        lines.append("=" * 80)
        lines.append("")

        for stage_name, stage in self.stages.items():
            lines.append(f"  {stage_name}")
            lines.append(f"    {self._render_progress_bar(stage)}")
            lines.append(f"    Processed: {stage.processed:,} / {stage.total:,}")
            lines.append(f"    Rate: {self._format_rate(stage.rate)}  |  Elapsed: {self._format_time(stage.elapsed_seconds)}")

            if stage.errors > 0:
                lines.append(f"    Errors: {stage.errors}")

            if stage.total > 0 and stage.rate > 0:
                remaining = (stage.total - stage.processed) / stage.rate
                lines.append(f"    ETA: {self._format_time(remaining)}")

            lines.append("")

        avg_rate = sum(self.rate_samples) / len(self.rate_samples) if self.rate_samples else 0
        lines.append(f"  Average Rate: {self._format_rate(avg_rate)}")
        lines.append("=" * 80)
        lines.append("")

        output = "\n".join(lines)
        print(output, flush=True)

    def finish(self, stage_name: str):
        """Mark a stage as finished."""
        if stage_name in self.stages:
            stage = self.stages[stage_name]
            stage.processed = stage.total
            self.render(force=True)

    def clear(self):
        """Clear the display."""
        sys.stdout.write('\033[2J\033[H')
        sys.stdout.flush()


_display_instance = None


def get_display() -> ProgressDisplay:
    """Get the global progress display instance."""
    global _display_instance
    if _display_instance is None:
        _display_instance = ProgressDisplay()
    return _display_instance


def create_stage(stage_name: str, total: int = 0):
    """Create a progress tracking stage."""
    get_display().create_stage(stage_name, total)


def update_stage(stage_name: str, processed: int = None, total: int = None, errors: int = None):
    """Update stage progress."""
    get_display().update_stage(stage_name, processed, total, errors)


def render():
    """Render the progress display."""
    get_display().render()


def finish(stage_name: str):
    """Mark stage as finished."""
    get_display().finish(stage_name)


def clear():
    """Clear the display."""
    get_display().clear()
