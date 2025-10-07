#!/usr/bin/env python3
"""ML Error Analyzer - Intelligent error pattern detection and analysis

This script uses machine learning (K-Means clustering) to automatically identify
patterns in pipeline errors, group similar errors together, and generate
plain-English reports with actionable recommendations.

Features:
- Automatic error clustering using K-Means
- URL pattern extraction (domain, path structure)
- Error type frequency analysis
- HTTP status code analysis
- Plain-English summaries with recommendations
- Results saved to PostgreSQL for tracking

Usage:
    python scripts/ml_error_analyzer.py
    python scripts/ml_error_analyzer.py --min-errors 50
    python scripts/ml_error_analyzer.py --clusters 5
"""

import logging
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.common.postgres_manager import get_postgres_manager

try:
    import pandas as pd
    from sklearn.cluster import KMeans
    from sklearn.preprocessing import LabelEncoder
except ImportError as e:
    print(f"❌ Missing dependencies: {e}")
    print("Install with: pip install pandas numpy scikit-learn")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

logger = logging.getLogger(__name__)


class ErrorAnalyzer:
    """Analyzes error logs using machine learning to identify patterns."""

    def __init__(self, postgres_manager):
        """Initialize analyzer with database connection.

        Args:
            postgres_manager: PostgresManager instance
        """
        self.db = postgres_manager

    def extract_url_features(self, url: str) -> dict[str, Any]:
        """Extract features from a URL for ML analysis.

        Args:
            url: URL to analyze

        Returns:
            Dictionary of extracted features
        """
        try:
            parsed = urlparse(url)
            path_parts = [p for p in parsed.path.split('/') if p]

            return {
                'domain': parsed.netloc,
                'path_depth': len(path_parts),
                'has_query': 1 if parsed.query else 0,
                'extension': path_parts[-1].split('.')[-1] if path_parts and '.' in path_parts[-1] else 'none',
                'is_subdomain': 1 if parsed.netloc.count('.') > 1 else 0
            }
        except Exception:
            return {
                'domain': 'unknown',
                'path_depth': 0,
                'has_query': 0,
                'extension': 'none',
                'is_subdomain': 0
            }

    def prepare_features(self, df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
        """Prepare features for clustering.

        Args:
            df: DataFrame with error logs

        Returns:
            Tuple of (feature DataFrame, metadata dict)
        """
        logger.info("Extracting features from error logs...")

        # Extract URL features
        url_features = df['url'].apply(self.extract_url_features)
        df_features = pd.DataFrame(url_features.tolist())

        # Encode error types
        error_encoder = LabelEncoder()
        df_features['error_type_encoded'] = error_encoder.fit_transform(df['error_type'])

        # Encode HTTP status codes (handle nulls)
        df_features['http_status'] = df['http_status_code'].fillna(0).astype(int)

        # Encode stage
        stage_encoder = LabelEncoder()
        df_features['stage_encoded'] = stage_encoder.fit_transform(df['stage'])

        # Select numeric features for clustering
        numeric_features = [
            'error_type_encoded',
            'http_status',
            'stage_encoded',
            'path_depth',
            'has_query',
            'is_subdomain'
        ]

        metadata = {
            'error_encoder': error_encoder,
            'stage_encoder': stage_encoder,
            'url_features': df_features
        }

        return df_features[numeric_features], metadata

    def determine_optimal_clusters(self, X: pd.DataFrame, max_clusters: int = 10) -> int:
        """Determine optimal number of clusters using elbow method.

        Args:
            X: Feature matrix
            max_clusters: Maximum clusters to try

        Returns:
            Optimal number of clusters
        """
        if len(X) < max_clusters:
            return min(3, len(X))

        # Use elbow method
        inertias = []
        K_range = range(2, min(max_clusters + 1, len(X)))

        for k in K_range:
            kmeans = KMeans(n_clusters=k, random_state=42, n_init=10)
            kmeans.fit(X)
            inertias.append(kmeans.inertia_)

        # Simple heuristic: find the "elbow"
        # Calculate rate of decrease
        if len(inertias) >= 3:
            decreases = [inertias[i] - inertias[i + 1] for i in range(len(inertias) - 1)]
            # Find where decrease slows significantly
            for i in range(1, len(decreases) - 1):
                if decreases[i] < 0.7 * decreases[i - 1]:
                    return i + 2  # +2 because we started at k=2

        # Default to 5 clusters or fewer if not enough data
        return min(5, len(X) // 10 + 2)

    def generate_cluster_summary(
        self,
        cluster_id: int,
        cluster_df: pd.DataFrame,
        total_errors: int,
        metadata: dict[str, Any]
    ) -> dict[str, Any]:
        """Generate a plain-English summary for an error cluster.

        Args:
            cluster_id: Cluster identifier
            cluster_df: DataFrame of errors in this cluster
            total_errors: Total number of errors analyzed
            metadata: Feature extraction metadata

        Returns:
            Dictionary with cluster analysis
        """
        cluster_size = len(cluster_df)
        cluster_pct = (cluster_size / total_errors) * 100

        # Most common error type
        error_types = cluster_df['error_type'].value_counts()
        common_error = error_types.index[0]
        error_freq = error_types.iloc[0]

        # Most common domain
        domains = cluster_df['url'].apply(lambda x: urlparse(x).netloc)
        domain_counts = domains.value_counts()
        common_domain = domain_counts.index[0] if len(domain_counts) > 0 else 'unknown'

        # Average HTTP status
        avg_status = cluster_df['http_status_code'].dropna().mean() if 'http_status_code' in cluster_df else None

        # Most common stage
        stage_counts = cluster_df['stage'].value_counts()
        common_stage = stage_counts.index[0]

        # Generate plain-English summary
        summary = f"Cluster {cluster_id + 1}: {cluster_size} errors ({cluster_pct:.1f}% of total)\n"
        summary += f"Primary Error: '{common_error}' ({error_freq} occurrences, {error_freq/cluster_size*100:.1f}%)\n"
        summary += f"Most Affected Domain: {common_domain}\n"
        summary += f"Pipeline Stage: {common_stage}\n"

        if avg_status and avg_status > 0:
            summary += f"Average HTTP Status: {avg_status:.0f}\n"

        # Generate recommendations based on error patterns
        recommendations = self._generate_recommendations(
            common_error, common_domain, avg_status, common_stage
        )

        return {
            'cluster_id': cluster_id,
            'cluster_size': cluster_size,
            'cluster_percentage': cluster_pct,
            'common_error_type': common_error,
            'common_url_pattern': common_domain,
            'avg_http_status': avg_status,
            'summary': summary,
            'recommendations': recommendations
        }

    def _generate_recommendations(
        self,
        error_type: str,
        domain: str,
        avg_status: float,
        stage: str
    ) -> str:
        """Generate actionable recommendations based on error patterns.

        Args:
            error_type: Most common error type
            domain: Most affected domain
            avg_status: Average HTTP status code
            stage: Pipeline stage

        Returns:
            Plain-English recommendations
        """
        recommendations = []

        # HTTP status code recommendations
        if avg_status:
            if 400 <= avg_status < 500:
                recommendations.append(
                    f"Client errors (4xx): Check URL validity, authentication, or rate limiting for {domain}"
                )
            elif 500 <= avg_status < 600:
                recommendations.append(
                    f"Server errors (5xx): Target server {domain} may be experiencing issues. "
                    "Consider retry logic or contact site administrators."
                )
            elif avg_status == 0 or avg_status is None:
                recommendations.append(
                    "Connection failures: Check network connectivity, DNS resolution, or firewall settings"
                )

        # Error type recommendations
        error_lower = error_type.lower()
        if 'timeout' in error_lower:
            recommendations.append(
                "Timeout errors: Increase timeout settings or reduce request rate"
            )
        elif 'connection' in error_lower:
            recommendations.append(
                "Connection errors: Verify network stability and target server availability"
            )
        elif 'ssl' in error_lower or 'certificate' in error_lower:
            recommendations.append(
                "SSL/Certificate errors: Update SSL certificates or disable verification for testing"
            )
        elif 'rate' in error_lower or 'throttle' in error_lower:
            recommendations.append(
                "Rate limiting detected: Implement exponential backoff or reduce request frequency"
            )

        # Stage-specific recommendations
        if stage == 'stage1':
            recommendations.append(
                "Stage 1 (Discovery): Consider adjusting Scrapy concurrency settings or adding delays"
            )
        elif stage == 'stage2':
            recommendations.append(
                "Stage 2 (Analysis): Verify content extraction logic and HTML parsing"
            )
        elif stage == 'stage3':
            recommendations.append(
                "Stage 3 (Summarization): Check model loading and memory availability"
            )

        return "\n".join(f"• {rec}" for rec in recommendations) if recommendations else "No specific recommendations available"

    def analyze(self, min_errors: int = 10, n_clusters: int | None = None) -> dict[str, Any]:
        """Perform ML-based error analysis.

        Args:
            min_errors: Minimum number of errors required for analysis
            n_clusters: Number of clusters (auto-determined if None)

        Returns:
            Analysis results dictionary
        """
        logger.info("=" * 80)
        logger.info("ML ERROR ANALYSIS")
        logger.info("=" * 80)

        # Fetch error logs
        logger.info("Fetching error logs from database...")
        error_logs = self.db.get_error_logs(limit=10000)

        if not error_logs:
            logger.warning("No error logs found in database")
            return {'status': 'no_data'}

        df = pd.DataFrame(error_logs)
        total_errors = len(df)

        logger.info(f"Loaded {total_errors} error records")

        if total_errors < min_errors:
            logger.warning(f"Insufficient errors for analysis (need at least {min_errors})")
            return {'status': 'insufficient_data', 'total_errors': total_errors}

        # Prepare features
        X, metadata = self.prepare_features(df)

        # Determine optimal clusters
        if n_clusters is None:
            n_clusters = self.determine_optimal_clusters(X)
            logger.info(f"Auto-determined optimal clusters: {n_clusters}")
        else:
            logger.info(f"Using specified cluster count: {n_clusters}")

        # Perform clustering
        logger.info("Performing K-Means clustering...")
        kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
        df['cluster'] = kmeans.fit_predict(X)

        # Analyze each cluster
        logger.info("Generating cluster summaries...")
        cluster_analyses = []

        for cluster_id in range(n_clusters):
            cluster_df = df[df['cluster'] == cluster_id]
            analysis = self.generate_cluster_summary(
                cluster_id, cluster_df, total_errors, metadata
            )
            cluster_analyses.append(analysis)

        # Sort by cluster size (largest first)
        cluster_analyses.sort(key=lambda x: x['cluster_size'], reverse=True)

        # Save to database
        logger.info("Saving analysis results to database...")
        self.db.save_error_analysis(total_errors, n_clusters, cluster_analyses)

        # Print summary
        logger.info("")
        logger.info("=" * 80)
        logger.info("ANALYSIS RESULTS")
        logger.info("=" * 80)
        logger.info(f"Total Errors Analyzed: {total_errors}")
        logger.info(f"Clusters Identified: {n_clusters}")
        logger.info("")

        for analysis in cluster_analyses:
            logger.info(analysis['summary'])
            logger.info("Recommendations:")
            logger.info(analysis['recommendations'])
            logger.info("-" * 80)

        return {
            'status': 'success',
            'total_errors': total_errors,
            'num_clusters': n_clusters,
            'clusters': cluster_analyses
        }


def main():
    """Main entry point for command-line usage."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Analyze pipeline errors using machine learning",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        '--min-errors',
        type=int,
        default=10,
        help='Minimum number of errors required for analysis (default: 10)'
    )
    parser.add_argument(
        '--clusters',
        type=int,
        default=None,
        help='Number of clusters (auto-determined if not specified)'
    )

    args = parser.parse_args()

    # Get PostgreSQL manager
    db = get_postgres_manager()
    if not db:
        logger.error("PostgreSQL not configured. Set DB_PASSWORD environment variable.")
        logger.error("Example: export DB_PASSWORD=your_password")
        sys.exit(1)

    # Run analysis
    try:
        analyzer = ErrorAnalyzer(db)
        result = analyzer.analyze(min_errors=args.min_errors, n_clusters=args.clusters)

        if result['status'] == 'no_data':
            logger.warning("No error data available for analysis")
            sys.exit(0)
        elif result['status'] == 'insufficient_data':
            logger.warning(f"Need at least {args.min_errors} errors, found {result['total_errors']}")
            sys.exit(0)

        logger.info("")
        logger.info("✅ Error analysis complete!")
        logger.info("Results saved to database (error_analysis_reports table)")

    except KeyboardInterrupt:
        logger.info("")
        logger.info("Analysis cancelled by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Analysis failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()