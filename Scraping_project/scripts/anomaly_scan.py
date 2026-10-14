#!/usr/bin/env python3
"""Offline z-score scan of a metrics fixture (#501).

Stdlib only. Prints an Alertmanager webhook payload and does not send it.
Does not read data/delta_lake and does not train on the live lake.
scripts/kmeans_anomaly_detector.py is loaded only when its imports already
succeed; a missing third-party library does not fail this process.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

KMEANS_IMPORTS = ("numpy", "sklearn", "pandas", "duckdb")


@dataclass(frozen=True)
class Series:
    metric: str
    threshold: float
    baseline: list[float]
    current: float
    latest_timestamp: str


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def assert_not_lake(path: Path) -> None:
    """Refuse the live lake without stating it. Path checks only."""
    lake = (project_root() / "data" / "delta_lake").resolve()
    resolved = path.resolve()
    if resolved == lake or lake in resolved.parents:
        print("refusing to read data/delta_lake", file=sys.stderr)
        raise SystemExit(2)


def as_float(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def load_series(path: Path, threshold_override: float | None) -> Series:
    assert_not_lake(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"fixture not found: {path}", file=sys.stderr)
        raise SystemExit(2) from None
    except json.JSONDecodeError as exc:
        print(f"fixture is not JSON: {path}: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    if not isinstance(raw, dict):
        print("fixture must be a JSON object", file=sys.stderr)
        raise SystemExit(2)

    metric = raw.get("metric", "metric")
    if not isinstance(metric, str) or not metric.strip():
        print("metric must be a non-empty string", file=sys.stderr)
        raise SystemExit(2)

    if threshold_override is None:
        try:
            threshold = as_float(raw.get("threshold", 3.0), "threshold")
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(2) from None
    else:
        threshold = threshold_override
    if threshold < 0:
        print("threshold must be >= 0", file=sys.stderr)
        raise SystemExit(2)

    points = raw.get("points")
    if not isinstance(points, list) or len(points) < 3:
        print("points must be a list of at least 3 samples", file=sys.stderr)
        raise SystemExit(2)

    values: list[float] = []
    latest_timestamp = ""
    for index, point in enumerate(points):
        if not isinstance(point, dict):
            print(f"points[{index}] must be an object", file=sys.stderr)
            raise SystemExit(2)
        try:
            values.append(as_float(point.get("value"), f"points[{index}].value"))
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(2) from None
        timestamp = point.get("timestamp", "")
        if timestamp is None:
            timestamp = ""
        if not isinstance(timestamp, str):
            print(f"points[{index}].timestamp must be a string", file=sys.stderr)
            raise SystemExit(2)
        latest_timestamp = timestamp

    return Series(
        metric=metric.strip(),
        threshold=threshold,
        baseline=values[:-1],
        current=values[-1],
        latest_timestamp=latest_timestamp,
    )


def sample_zscore(baseline: list[float], current: float) -> tuple[float, float, float]:
    count = len(baseline)
    mean = sum(baseline) / count
    variance = sum((value - mean) ** 2 for value in baseline) / (count - 1)
    stdev = math.sqrt(variance)
    if stdev == 0.0:
        score = 0.0 if current == mean else math.inf
    else:
        score = abs(current - mean) / stdev
    return mean, stdev, score


def format_score(score: float) -> str:
    if math.isinf(score):
        return "inf"
    return f"{score:.4f}"


def webhook_payload(series: Series, score: float) -> dict[str, object]:
    summary = f"{series.metric} z-score {format_score(score)} exceeds {series.threshold:.4f}"
    labels = {
        "alertname": "MetricAnomaly",
        "severity": "warning",
        "metric": series.metric,
    }
    return {
        "version": "4",
        "status": "firing",
        "receiver": "anomaly-scan-stub",
        "groupLabels": {"alertname": "MetricAnomaly"},
        "commonLabels": labels,
        "commonAnnotations": {
            "summary": summary,
            "zscore": format_score(score),
            "threshold": f"{series.threshold:.4f}",
        },
        "externalURL": "",
        "alerts": [
            {
                "status": "firing",
                "labels": labels,
                "annotations": {
                    "summary": summary,
                    "latest_timestamp": series.latest_timestamp,
                    "latest_value": f"{series.current:.6g}",
                },
                "startsAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "endsAt": "0001-01-01T00:00:00Z",
                "generatorURL": "",
            }
        ],
    }


def print_webhook(series: Series, score: float) -> None:
    """Stub only: show the body a webhook would POST. No HTTP client."""
    print("alertmanager webhook stub (not sent):")
    print(json.dumps(webhook_payload(series, score), indent=2, sort_keys=True))
    if os.environ.get("ALERTMANAGER_URL"):
        print("alertmanager webhook stub: ALERTMANAGER_URL is set and was not called")


def maybe_load_kmeans() -> None:
    """Load the existing detector only when its imports already succeed.

    Do not execute that file as a script: its main() reads data/delta_lake.
    A failed or skipped load does not change this process's exit status.
    """
    missing = [name for name in KMEANS_IMPORTS if importlib.util.find_spec(name) is None]
    if missing:
        print("kmeans_anomaly_detector: skipped (missing " + ", ".join(missing) + "); stdlib scan only")
        return

    script = Path(__file__).resolve().with_name("kmeans_anomaly_detector.py")
    probe = (
        "import importlib.util, sys\n"
        "from pathlib import Path\n"
        "path = Path(sys.argv[1])\n"
        "spec = importlib.util.spec_from_file_location('kmeans_anomaly_detector', path)\n"
        "if spec is None or spec.loader is None:\n"
        "    sys.exit(1)\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n"
        "detector = mod.ErrorAnomalyDetector(n_clusters=3)\n"
        "if detector.is_fitted:\n"
        "    sys.exit(1)\n"
        "print('kmeans_anomaly_detector: loaded n_clusters=%s; not training; not reading data/delta_lake' % detector.n_clusters)\n"
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-c", probe, str(script)],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"kmeans_anomaly_detector: skipped ({exc}); stdlib scan only")
        return
    if proc.returncode != 0:
        print("kmeans_anomaly_detector: skipped (import failed); stdlib scan only")
        return
    message = proc.stdout.strip()
    if message:
        print(message)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline metric anomaly scan (#501)")
    parser.add_argument("--fixture", required=True, help="Metrics JSON fixture")
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="Override the fixture z-score threshold",
    )
    args = parser.parse_args(argv)
    if args.threshold is not None and (math.isnan(args.threshold) or math.isinf(args.threshold)):
        print("threshold must be finite", file=sys.stderr)
        return 2

    fixture = Path(args.fixture)
    series = load_series(fixture, args.threshold)
    mean, stdev, score = sample_zscore(series.baseline, series.current)
    anomaly = score > series.threshold

    maybe_load_kmeans()

    print(f"fixture: {fixture}")
    print(f"metric: {series.metric}")
    print(f"points: {len(series.baseline) + 1}")
    print(f"baseline_mean: {mean:.6f}")
    print(f"baseline_stdev: {stdev:.6f}")
    print(f"latest: {series.current:.6g}")
    print(f"zscore: {format_score(score)}")
    print(f"threshold: {series.threshold:.4f}")
    if anomaly:
        print("result: anomaly")
        print_webhook(series, score)
        return 1
    print("result: ok")
    print("alertmanager webhook stub: not firing")
    if os.environ.get("ALERTMANAGER_URL"):
        print("alertmanager webhook stub: ALERTMANAGER_URL is set and was not called")
    return 0


if __name__ == "__main__":
    sys.exit(main())
