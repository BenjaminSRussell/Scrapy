#!/usr/bin/env python3
"""
A simple PromQL tokenizer to extract metric names from expressions.
"""

import re
from typing import Set

# Regex to find potential metric names in a PromQL query.
# This pattern is designed to capture metric names and ignore labels in `by (label)` clauses.
METRIC_NAME_PATTERN = re.compile(r"\b([a-zA-Z_:][a-zA-Z0-9_:]+)\b(?!\s*=[~\s]*[\"'])")


# Whitelist of common PromQL functions and keywords to ignore.
PROMQL_KEYWORDS = {
    "sum", "rate", "increase", "histogram_quantile", "avg", "count",
    "min", "max", "stddev", "stdvar", "topk", "bottomk", "quantile",
    "by", "without", "on", "group_left", "group_right",
    "and", "or", "unless",
    "__name__", "m", "s", "d", "h", "y", "error_type",
}

def tokenize_promql(expr: str) -> Set[str]:
    """
    Tokenizes a PromQL expression and returns a set of potential metric names.

    Args:
        expr: The PromQL expression string.

    Returns:
        A set of tokens that could be metric names.
    """
    # Remove content within `by (...)` and `without (...)` clauses to avoid capturing labels
    expr = re.sub(r"\b(by|without)\s*\([^)]+\)", "", expr)
    tokens = set(METRIC_NAME_PATTERN.findall(expr))
    return {token for token in tokens if token not in PROMQL_KEYWORDS and not token.isdigit()}
