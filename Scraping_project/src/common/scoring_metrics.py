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
        >>> assert 0.99 < score <= 1.0

        >>> month_ago = now - timedelta(days=30)
        >>> score = calculate_decay_score(month_ago, reference_date=now, decay_constant=0.01)
        >>> assert 0.70 < score < 0.75

        >>> year_ago = now - timedelta(days=365)
        >>> score = calculate_decay_score(year_ago, reference_date=now, decay_constant=0.01)
        >>> assert 0.02 < score < 0.03
    """
    if isinstance(publication_date, str):
        if publication_date.endswith("Z"):
            publication_date = publication_date[:-1] + "+00:00"
        publication_date = datetime.fromisoformat(publication_date)

    if publication_date.tzinfo is None:
        publication_date = publication_date.replace(tzinfo=UTC)

    if reference_date is None:
        reference_date = datetime.now(UTC)
    elif isinstance(reference_date, str):
        if reference_date.endswith("Z"):
            reference_date = reference_date[:-1] + "+00:00"
        reference_date = datetime.fromisoformat(reference_date)

    if reference_date.tzinfo is None:
        reference_date = reference_date.replace(tzinfo=UTC)

    time_delta = reference_date - publication_date

    if time_delta.total_seconds() < 0:
        raise ValueError(f"publication_date {publication_date} is in the future (reference_date: {reference_date})")

    days_elapsed = time_delta.total_seconds() / 86400.0

    score = math.exp(-decay_constant * days_elapsed)

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
        >>> values = [100.0, 80.0, 60.0]
        >>> recency_scores = [0.5, 0.8, 1.0]
        >>> avg = calculate_weighted_average(values, recency_scores)
        >>> assert 70 < avg < 90
    """
    if not values or not recency_scores:
        raise ValueError("values and recency_scores cannot be empty")

    if len(values) != len(recency_scores):
        raise ValueError(
            f"values and recency_scores must have same length (got {len(values)} and {len(recency_scores)})"
        )

    for score in recency_scores:
        if not 0.0 <= score <= 1.0:
            raise ValueError(f"recency_score {score} must be in range [0.0, 1.0]")

    weighted_sum = sum(v * w for v, w in zip(values, recency_scores, strict=False))
    total_weight = sum(recency_scores)

    if total_weight == 0:
        return sum(values) / len(values)

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
        >>> assert ranked[0]["url"] == "new.com"
        >>> assert ranked[0]["recency_score"] > ranked[1]["recency_score"]
    """
    for item in items:
        if publication_date_field not in item:
            raise ValueError(f"Item missing required field '{publication_date_field}': {item}")

    reference_date = datetime.now(UTC)
    for item in items:
        pub_date = item[publication_date_field]
        item[score_field] = calculate_decay_score(
            pub_date,
            reference_date=reference_date,
            decay_constant=decay_constant,
        )

    return sorted(items, key=lambda x: x[score_field], reverse=True)

def get_decay_half_life(decay_constant: float) -> float:
    if decay_constant <= 0:
        raise ValueError(f"decay_constant must be positive (got {decay_constant})")

    return math.log(2) / decay_constant
