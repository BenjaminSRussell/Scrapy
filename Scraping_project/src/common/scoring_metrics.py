"""Recency-weighted scoring functions for temporal relevance.

This module implements exponential decay scoring to calculate chronological
relevance of scraped items. Fresher content receives higher scores, enabling
recency-weighted aggregation and summarization.
"""

import math
from datetime import UTC, datetime


def calculate_decay_score(
    publication_date: datetime | str,
    reference_date: datetime | str | None = None,
    decay_constant: float = 0.01,
) -> float:
    """Calculate exponential decay score based on publication date.

    Implements the exponential decay model: S = e^(-k * T)
    where:
    - S = score [0.0, 1.0]
    - k = decay constant (controls how quickly scores decay)
    - T = time difference in days

    Args:
        publication_date: Date when content was published (datetime or ISO string)
        reference_date: Reference date for scoring (defaults to current UTC time)
        decay_constant: Decay rate parameter (k). Higher values = faster decay.
                       Default 0.01 means ~63% score after 100 days.

    Returns:
        Decay score in range [0.0, 1.0], clamped to prevent exceeding 1.0

    Raises:
        ValueError: If publication_date is in the future or invalid format

    Examples:
        >>> from datetime import datetime, timedelta
        >>> now = datetime(2024, 1, 20, tzinfo=timezone.utc)
        >>> yesterday = now - timedelta(days=1)
        >>> score = calculate_decay_score(yesterday, reference_date=now, decay_constant=0.01)
        >>> assert 0.99 < score <= 1.0  # Very recent content

        >>> month_ago = now - timedelta(days=30)
        >>> score = calculate_decay_score(month_ago, reference_date=now, decay_constant=0.01)
        >>> assert 0.70 < score < 0.75  # Moderate decay

        >>> year_ago = now - timedelta(days=365)
        >>> score = calculate_decay_score(year_ago, reference_date=now, decay_constant=0.01)
        >>> assert 0.02 < score < 0.03  # Significant decay
    """
    # Parse publication_date if string
    if isinstance(publication_date, str):
        if publication_date.endswith("Z"):
            publication_date = publication_date[:-1] + "+00:00"
        publication_date = datetime.fromisoformat(publication_date)

    # Ensure publication_date is timezone-aware
    if publication_date.tzinfo is None:
        publication_date = publication_date.replace(tzinfo=UTC)

    # Parse or default reference_date
    if reference_date is None:
        reference_date = datetime.now(UTC)
    elif isinstance(reference_date, str):
        if reference_date.endswith("Z"):
            reference_date = reference_date[:-1] + "+00:00"
        reference_date = datetime.fromisoformat(reference_date)

    # Ensure reference_date is timezone-aware
    if reference_date.tzinfo is None:
        reference_date = reference_date.replace(tzinfo=UTC)

    # Calculate time difference in days
    time_delta = reference_date - publication_date

    # Validate that publication_date is not in the future
    if time_delta.total_seconds() < 0:
        raise ValueError(
            f"publication_date {publication_date} is in the future "
            f"(reference_date: {reference_date})"
        )

    # Convert to days (handle unit consistency)
    days_elapsed = time_delta.total_seconds() / 86400.0  # 86400 seconds in a day

    # Calculate exponential decay score
    score = math.exp(-decay_constant * days_elapsed)

    # Clamp to [0.0, 1.0] to prevent edge cases from exceeding range
    return min(max(score, 0.0), 1.0)


def calculate_weighted_average(
    values: list[float],
    recency_scores: list[float],
) -> float:
    """Calculate weighted average using recency scores as weights.

    Args:
        values: List of numeric values to average
        recency_scores: List of recency scores (weights) corresponding to values

    Returns:
        Weighted average value

    Raises:
        ValueError: If lists are empty or have different lengths
        ValueError: If any recency_score is not in [0.0, 1.0]

    Examples:
        >>> values = [100.0, 80.0, 60.0]  # Older to newer values
        >>> recency_scores = [0.5, 0.8, 1.0]  # Corresponding recency
        >>> avg = calculate_weighted_average(values, recency_scores)
        >>> assert 70 < avg < 90  # Weighted toward newer values
    """
    if not values or not recency_scores:
        raise ValueError("values and recency_scores cannot be empty")

    if len(values) != len(recency_scores):
        raise ValueError(
            f"values and recency_scores must have same length "
            f"(got {len(values)} and {len(recency_scores)})"
        )

    # Validate recency scores
    for score in recency_scores:
        if not 0.0 <= score <= 1.0:
            raise ValueError(f"recency_score {score} must be in range [0.0, 1.0]")

    # Calculate weighted sum and total weight
    weighted_sum = sum(v * w for v, w in zip(values, recency_scores, strict=False))
    total_weight = sum(recency_scores)

    # Avoid division by zero
    if total_weight == 0:
        return sum(values) / len(values)  # Fallback to simple average

    return weighted_sum / total_weight


def calculate_temporal_relevance_rank(
    items: list[dict],
    publication_date_field: str = "publication_date",
    score_field: str = "recency_score",
    decay_constant: float = 0.01,
) -> list[dict]:
    """Rank items by temporal relevance using exponential decay.

    Adds recency_score to each item and sorts by descending score.
    This is useful for prioritizing fresher content in aggregations.

    Args:
        items: List of item dictionaries
        publication_date_field: Field name containing publication date
        score_field: Field name to write recency score to
        decay_constant: Decay rate parameter

    Returns:
        List of items sorted by recency_score (descending), with scores added

    Raises:
        ValueError: If any item is missing publication_date_field

    Examples:
        >>> items = [
        ...     {"url": "old.com", "publication_date": "2023-01-01T00:00:00Z"},
        ...     {"url": "new.com", "publication_date": "2024-01-15T00:00:00Z"},
        ... ]
        >>> ranked = calculate_temporal_relevance_rank(items)
        >>> assert ranked[0]["url"] == "new.com"  # Newer item ranked first
        >>> assert ranked[0]["recency_score"] > ranked[1]["recency_score"]
    """
    # Validate all items have publication_date
    for item in items:
        if publication_date_field not in item:
            raise ValueError(
                f"Item missing required field '{publication_date_field}': {item}"
            )

    # Calculate and add recency scores
    reference_date = datetime.now(UTC)
    for item in items:
        pub_date = item[publication_date_field]
        item[score_field] = calculate_decay_score(
            pub_date,
            reference_date=reference_date,
            decay_constant=decay_constant,
        )

    # Sort by recency_score descending (fresher content first)
    return sorted(items, key=lambda x: x[score_field], reverse=True)


def get_decay_half_life(decay_constant: float) -> float:
    """Calculate half-life (days until score reaches 0.5) for given decay constant.

    This is useful for understanding the practical impact of different decay
    constants when configuring the scoring system.

    Args:
        decay_constant: Decay rate parameter (k)

    Returns:
        Number of days until score reaches 0.5

    Examples:
        >>> half_life = get_decay_half_life(0.01)
        >>> assert 69 < half_life < 70  # ~69.3 days

        >>> half_life = get_decay_half_life(0.1)
        >>> assert 6 < half_life < 7  # ~6.93 days
    """
    if decay_constant <= 0:
        raise ValueError(f"decay_constant must be positive (got {decay_constant})")

    return math.log(2) / decay_constant
