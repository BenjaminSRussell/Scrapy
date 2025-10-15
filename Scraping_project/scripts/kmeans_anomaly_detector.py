#!/usr/bin/env python3
"""K-Means based anomaly detection for pipeline errors.

Clusters error patterns and detects anomalies in real-time using machine learning.
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

try:
    from sklearn.cluster import KMeans
    from sklearn.preprocessing import StandardScaler
except ImportError:
    print("❌ scikit-learn not installed. Run: pip install scikit-learn")
    sys.exit(1)

try:
    import pandas as pd
except ImportError:
    print("❌ pandas not installed. Run: pip install pandas")
    sys.exit(1)

try:
    import duckdb
except ImportError:
    print("❌ duckdb not installed. Run: pip install duckdb")
    sys.exit(1)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ErrorAnomalyDetector:
    """Detect anomalous error patterns using K-Means clustering."""

    def __init__(self, n_clusters=3):
        self.n_clusters = n_clusters
        self.kmeans = KMeans(n_clusters=n_clusters, random_state=42)
        self.scaler = StandardScaler()
        self.is_fitted = False
        self.feature_names = []

    def prepare_features(self, errors_df):
        """Prepare error features for clustering.

        Features extracted:
        1. Error rate (errors per minute)
        2. Unique error types
        3. Error type diversity (entropy)
        4. Temporal pattern (hour of day)
        5. Error code distribution
        """
        if errors_df.empty:
            return np.array([]).reshape(0, 5)

        # Ensure timestamp column exists and is datetime
        if "timestamp" not in errors_df.columns:
            errors_df["timestamp"] = datetime.now()
        else:
            errors_df["timestamp"] = pd.to_datetime(errors_df["timestamp"])

        # Feature 1: Error rate (errors/minute)
        time_range_minutes = (
            errors_df["timestamp"].max() - errors_df["timestamp"].min()
        ).total_seconds() / 60
        error_rate = len(errors_df) / max(time_range_minutes, 1)

        # Feature 2: Unique error types
        unique_types = (
            errors_df["error_type"].nunique()
            if "error_type" in errors_df.columns
            else 1
        )

        # Feature 3: Error type diversity (normalized entropy)
        if "error_type" in errors_df.columns:
            type_counts = errors_df["error_type"].value_counts()
            probs = type_counts / type_counts.sum()
            entropy = -np.sum(probs * np.log2(probs + 1e-10))
            normalized_entropy = (
                entropy / np.log2(len(type_counts) + 1e-10)
                if len(type_counts) > 1
                else 0
            )
        else:
            normalized_entropy = 0

        # Feature 4: Peak hour (time pattern)
        errors_df["hour"] = errors_df["timestamp"].dt.hour
        peak_hour_count = errors_df.groupby("hour").size().max()

        # Feature 5: Error code variance
        if "error_code" in errors_df.columns:
            error_code_variance = errors_df["error_code"].var()
        else:
            error_code_variance = 0

        # Create feature vector
        features = np.array(
            [
                error_rate,
                unique_types,
                normalized_entropy,
                peak_hour_count,
                error_code_variance,
            ]
        ).reshape(1, -1)

        self.feature_names = [
            "error_rate",
            "unique_types",
            "entropy",
            "peak_hour_count",
            "error_code_variance",
        ]

        return features

    def fit(self, errors_df):
        """Fit K-Means model on historical error data."""
        logger.info(
            f"Fitting K-Means with {self.n_clusters} clusters on {len(errors_df)} error records"
        )

        # Create multiple time windows for training
        errors_df["timestamp"] = pd.to_datetime(errors_df["timestamp"])
        min_time = errors_df["timestamp"].min()
        max_time = errors_df["timestamp"].max()

        # Create hourly windows
        windows = []
        current_time = min_time
        while current_time < max_time:
            window_end = current_time + timedelta(hours=1)
            window_data = errors_df[
                (errors_df["timestamp"] >= current_time)
                & (errors_df["timestamp"] < window_end)
            ]

            if not window_data.empty:
                windows.append(window_data)

            current_time = window_end

        if not windows:
            logger.warning("No time windows created - insufficient data")
            return False

        # Extract features from each window
        X_list = []
        for window in windows:
            features = self.prepare_features(window)
            if features.size > 0:
                X_list.append(features)

        if not X_list:
            logger.warning("No features extracted")
            return False

        X = np.vstack(X_list)

        # Scale features
        X_scaled = self.scaler.fit_transform(X)

        # Fit K-Means
        self.kmeans.fit(X_scaled)
        self.is_fitted = True

        logger.info(f"✅ Fitted K-Means with {self.n_clusters} clusters")
        logger.info(f"   Training windows: {len(windows)}")
        logger.info(f"   Feature dimensions: {X.shape[1]}")

        return True

    def detect_anomalies(self, current_errors_df, sensitivity=2.0):
        """Detect if current errors are anomalous.

        Args:
            current_errors_df: DataFrame with current error data
            sensitivity: Anomaly threshold multiplier (default: 2.0 = 2 std devs)

        Returns:
            Dictionary with anomaly detection results
        """
        if not self.is_fitted:
            raise ValueError("Model not fitted. Call fit() first.")

        if current_errors_df.empty:
            return {
                "is_anomaly": False,
                "anomaly_score": 0,
                "threshold": 0,
                "cluster": -1,
                "details": {"message": "No current errors"},
            }

        X = self.prepare_features(current_errors_df)

        if X.size == 0:
            return {
                "is_anomaly": False,
                "anomaly_score": 0,
                "threshold": 0,
                "cluster": -1,
                "details": {"message": "No features extracted"},
            }

        X_scaled = self.scaler.transform(X)

        # Predict cluster
        cluster = self.kmeans.predict(X_scaled)[0]

        # Calculate distance to nearest cluster center
        distances = self.kmeans.transform(X_scaled)
        min_distance = distances.min(axis=1)[0]

        # Anomaly threshold (sensitivity * standard deviations from cluster center)
        # Calculate from training data
        all_distances = []
        for center_idx in range(self.n_clusters):
            dist_to_center = np.linalg.norm(
                self.kmeans.cluster_centers_ - self.kmeans.cluster_centers_[center_idx],
                axis=1,
            )
            all_distances.extend(dist_to_center)

        threshold = sensitivity * np.std(all_distances)
        is_anomaly = min_distance > threshold

        # Feature importance (which features contribute most to anomaly)
        feature_scores = {}
        if len(self.feature_names) == X.shape[1]:
            for idx, name in enumerate(self.feature_names):
                feature_scores[name] = float(X[0, idx])

        return {
            "is_anomaly": bool(is_anomaly),
            "anomaly_score": float(min_distance),
            "threshold": float(threshold),
            "cluster": int(cluster),
            "sensitivity": sensitivity,
            "details": {
                "current_error_count": len(current_errors_df),
                "expected_cluster": int(cluster),
                "distance_from_cluster": float(min_distance),
                "feature_scores": feature_scores,
            },
        }

    def save_model(self, filepath):
        """Save fitted model to disk."""
        import pickle

        if not self.is_fitted:
            raise ValueError("Cannot save unfitted model")

        model_data = {
            "kmeans": self.kmeans,
            "scaler": self.scaler,
            "n_clusters": self.n_clusters,
            "feature_names": self.feature_names,
        }

        with open(filepath, "wb") as f:
            pickle.dump(model_data, f)

        logger.info(f"✅ Model saved to {filepath}")

    def load_model(self, filepath):
        """Load fitted model from disk."""
        import pickle

        with open(filepath, "rb") as f:
            model_data = pickle.load(f)

        self.kmeans = model_data["kmeans"]
        self.scaler = model_data["scaler"]
        self.n_clusters = model_data["n_clusters"]
        self.feature_names = model_data.get("feature_names", [])
        self.is_fitted = True

        logger.info(f"✅ Model loaded from {filepath}")


def main():
    parser = argparse.ArgumentParser(description="K-Means anomaly detection for errors")
    parser.add_argument(
        "--train", action="store_true", help="Train model on historical data"
    )
    parser.add_argument(
        "--detect", action="store_true", help="Detect anomalies in current data"
    )
    parser.add_argument(
        "--model-file",
        default="data/models/anomaly_detector.pkl",
        help="Model file path",
    )
    parser.add_argument(
        "--sensitivity",
        type=float,
        default=2.0,
        help="Anomaly sensitivity (default: 2.0)",
    )
    parser.add_argument(
        "--output",
        default="exports/anomaly_report.json",
        help="Output file for detection results",
    )
    args = parser.parse_args()

    detector = ErrorAnomalyDetector(n_clusters=3)

    # Connect to DuckDB
    con = duckdb.connect(":memory:")

    if args.train:
        print("=" * 80)
        print("TRAINING K-MEANS ANOMALY DETECTOR")
        print("=" * 80)

        # Load historical errors from Delta Lake
        try:
            historical_errors = con.execute(
                """
                SELECT
                    _ingestion_time as timestamp,
                    error_code,
                    COALESCE(error_msg, error_reason, 'unknown') as error_type
                FROM read_parquet('data/delta_lake/stage1_errors/*.parquet')
                WHERE _ingestion_time >= CURRENT_TIMESTAMP - INTERVAL '7 days'
            """
            ).fetchdf()
        except Exception as e:
            logger.error(f"Failed to load historical errors: {e}")
            print(
                "⚠️  No historical error data found. Generating synthetic training data..."
            )

            # Generate synthetic data for demonstration
            np.random.seed(42)
            dates = pd.date_range(end=datetime.now(), periods=1000, freq="1H")
            historical_errors = pd.DataFrame(
                {
                    "timestamp": dates,
                    "error_code": np.random.choice([404, 500, 503], 1000),
                    "error_type": np.random.choice(
                        ["timeout", "not_found", "server_error"], 1000
                    ),
                }
            )

        print(f"Training on {len(historical_errors)} historical error records")

        success = detector.fit(historical_errors)

        if success:
            # Save model
            Path(args.model_file).parent.mkdir(parents=True, exist_ok=True)
            detector.save_model(args.model_file)
            print(f"\n✅ Training complete! Model saved to {args.model_file}")
        else:
            print("\n❌ Training failed - insufficient data")
            sys.exit(1)

    if args.detect:
        print("=" * 80)
        print("DETECTING ANOMALIES")
        print("=" * 80)

        # Load model
        if Path(args.model_file).exists():
            detector.load_model(args.model_file)
        else:
            print(f"❌ Model file not found: {args.model_file}")
            print("   Run with --train first to create the model")
            sys.exit(1)

        # Load current errors
        try:
            current_errors = con.execute(
                """
                SELECT
                    _ingestion_time as timestamp,
                    error_code,
                    COALESCE(error_msg, error_reason, 'unknown') as error_type
                FROM read_parquet('data/delta_lake/stage1_errors/*.parquet')
                WHERE _ingestion_time >= CURRENT_TIMESTAMP - INTERVAL '1 hour'
            """
            ).fetchdf()
        except Exception as e:
            logger.error(f"Failed to load current errors: {e}")
            print("⚠️  No current error data found")
            sys.exit(0)

        if current_errors.empty:
            print("✅ No errors in the last hour")
            sys.exit(0)

        print(f"Analyzing {len(current_errors)} current errors...")

        result = detector.detect_anomalies(current_errors, sensitivity=args.sensitivity)

        # Save result
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w") as f:
            json.dump(result, f, indent=2)

        # Print result
        print("\n" + "=" * 80)
        if result["is_anomaly"]:
            print("🚨 ANOMALY DETECTED!")
            print(f"   Anomaly score: {result['anomaly_score']:.4f}")
            print(f"   Threshold: {result['threshold']:.4f}")
            print(f"   Cluster: {result['cluster']}")
            print(f"   Sensitivity: {result['sensitivity']}")
            print("\n   Details:")
            for key, value in result["details"].items():
                if key != "feature_scores":
                    print(f"     {key}: {value}")

            if "feature_scores" in result["details"]:
                print("\n   Feature Contributions:")
                for feat, score in result["details"]["feature_scores"].items():
                    print(f"     {feat}: {score:.4f}")

            print(f"\n   📝 Full report saved to: {args.output}")
            print("\n   🚨 RECOMMENDED ACTION: Investigate error patterns!")

        else:
            print("✅ Error patterns are NORMAL")
            print(f"   Anomaly score: {result['anomaly_score']:.4f}")
            print(f"   Threshold: {result['threshold']:.4f}")
            print(f"   Cluster: {result['cluster']}")

        print("=" * 80)

    con.close()


if __name__ == "__main__":
    main()
