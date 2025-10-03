"""
Priority Queue Manager for URL Processing

Supports multiple ordering strategies:
- score-ordered: Sort by importance_score (default)
- fifo: First-in-first-out (original order)
- depth-first: Process shallow URLs first
- random: Random shuffle for testing

Ablation flags enable A/B testing of queue strategies.
"""

import logging
import random
from dataclasses import dataclass
from enum import Enum

from src.common.logging import get_structured_logger

logger = get_structured_logger(__name__, component="priority_queue")


class QueueStrategy(Enum):
    """Queue ordering strategies"""
    SCORE_ORDERED = "score-ordered"  # Sort by importance_score (high to low)
    FIFO = "fifo"  # First-in-first-out
    DEPTH_FIRST = "depth-first"  # Shallow URLs first
    RANDOM = "random"  # Random shuffle


@dataclass
class QueueItem:
    """Item in priority queue with importance metadata"""
    url: str
    url_hash: str
    importance_score: float = 0.0
    discovery_depth: int = 0
    discovery_source: str = "unknown"
    anchor_text: str | None = None
    is_same_domain: bool = True

    # Additional metadata
    insertion_order: int = 0  # For FIFO ordering


class PriorityQueueManager:
    """
    Manages URL queue with configurable ordering strategies.

    Supports ablation testing to compare strategies.
    """

    def __init__(
        self,
        strategy: QueueStrategy = QueueStrategy.SCORE_ORDERED,
        enable_ablation: bool = False,
        ablation_split: float = 0.5
    ):
        """
        Initialize priority queue manager.

        Args:
            strategy: Queue ordering strategy
            enable_ablation: Enable A/B testing with FIFO comparison
            ablation_split: Fraction of URLs to process with FIFO (for ablation)
        """
        self.strategy = strategy
        self.enable_ablation = enable_ablation
        self.ablation_split = ablation_split

        # Statistics
        self.total_queued = 0
        self.total_processed = 0
        self.score_ordered_count = 0
        self.fifo_count = 0

        logger.log_with_context(
            logging.INFO,
            "Priority queue initialized",
            strategy=strategy.value,
            enable_ablation=enable_ablation,
            ablation_split=ablation_split if enable_ablation else None
        )

    def order_batch(self, items: list[QueueItem]) -> list[QueueItem]:
        """
        Order a batch of queue items according to strategy.

        Args:
            items: List of queue items to order

        Returns:
            Ordered list of queue items
        """
        if not items:
            return items

        self.total_queued += len(items)

        # Assign insertion order for FIFO
        for i, item in enumerate(items):
            if item.insertion_order == 0:
                item.insertion_order = self.total_queued + i

        # Ablation: split batch between score-ordered and FIFO
        if self.enable_ablation:
            return self._ablation_split_batch(items)

        # Apply selected strategy
        return self._apply_strategy(items, self.strategy)

    def _apply_strategy(
        self,
        items: list[QueueItem],
        strategy: QueueStrategy
    ) -> list[QueueItem]:
        """Apply ordering strategy to items"""

        if strategy == QueueStrategy.SCORE_ORDERED:
            # Sort by importance score (descending)
            sorted_items = sorted(
                items,
                key=lambda x: x.importance_score,
                reverse=True
            )
            self.score_ordered_count += len(items)

            # Log top scores
            top_scores = [item.importance_score for item in sorted_items[:10]]
            logger.log_with_context(
                logging.DEBUG,
                "Batch ordered by importance score",
                batch_size=len(items),
                top_10_scores=[f"{s:.4f}" for s in top_scores],
                strategy="score-ordered"
            )

            return sorted_items

        elif strategy == QueueStrategy.FIFO:
            # Sort by insertion order
            sorted_items = sorted(items, key=lambda x: x.insertion_order)
            self.fifo_count += len(items)
            return sorted_items

        elif strategy == QueueStrategy.DEPTH_FIRST:
            # Sort by depth (ascending), then score (descending)
            return sorted(
                items,
                key=lambda x: (x.discovery_depth, -x.importance_score)
            )

        elif strategy == QueueStrategy.RANDOM:
            # Random shuffle
            shuffled = items.copy()
            random.shuffle(shuffled)
            return shuffled

        else:
            # Default to FIFO
            return sorted(items, key=lambda x: x.insertion_order)

    def _ablation_split_batch(self, items: list[QueueItem]) -> list[QueueItem]:
        """
        Split batch for ablation testing.

        First portion uses score-ordering, second uses FIFO.
        This enables comparison of strategy effectiveness.
        """
        split_index = int(len(items) * self.ablation_split)

        # First portion: score-ordered
        score_ordered_items = self._apply_strategy(
            items[:split_index],
            QueueStrategy.SCORE_ORDERED
        )

        # Second portion: FIFO
        fifo_items = self._apply_strategy(
            items[split_index:],
            QueueStrategy.FIFO
        )

        # Combine
        combined = score_ordered_items + fifo_items

        logger.log_with_context(
            logging.INFO,
            "Ablation split applied",
            total_items=len(items),
            score_ordered_count=len(score_ordered_items),
            fifo_count=len(fifo_items),
            split_ratio=self.ablation_split
        )

        return combined

    def get_statistics(self) -> dict:
        """Get queue statistics for reporting"""
        return {
            'strategy': self.strategy.value,
            'enable_ablation': self.enable_ablation,
            'total_queued': self.total_queued,
            'total_processed': self.total_processed,
            'score_ordered_count': self.score_ordered_count,
            'fifo_count': self.fifo_count
        }


def create_queue_manager_from_config(config: dict) -> PriorityQueueManager:
    """
    Create priority queue manager from configuration.

    Config keys:
        - queue_strategy: "score-ordered", "fifo", "depth-first", "random"
        - enable_queue_ablation: true/false
        - queue_ablation_split: 0.0-1.0 (default: 0.5)
    """
    strategy_name = config.get('queue_strategy', 'score-ordered')
    enable_ablation = config.get('enable_queue_ablation', False)
    ablation_split = config.get('queue_ablation_split', 0.5)

    # Map string to enum
    strategy_map = {
        'score-ordered': QueueStrategy.SCORE_ORDERED,
        'fifo': QueueStrategy.FIFO,
        'depth-first': QueueStrategy.DEPTH_FIRST,
        'random': QueueStrategy.RANDOM
    }

    strategy = strategy_map.get(strategy_name, QueueStrategy.SCORE_ORDERED)

    return PriorityQueueManager(
        strategy=strategy,
        enable_ablation=enable_ablation,
        ablation_split=ablation_split
    )
