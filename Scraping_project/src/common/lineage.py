"""Data lineage tracking and visualization for the pipeline.

Tracks the flow of data through the three stages and provides
utilities for lineage visualization and verification.
"""

import json
import logging
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class LineageNode:
    """Represents a node in the data lineage graph."""
    stage: str
    url: str
    url_hash: str
    timestamp: str
    input_file: str | None = None
    output_file: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass
class LineageEdge:
    """Represents an edge connecting two lineage nodes."""
    from_hash: str
    to_hash: str
    transformation: str
    timestamp: str


class LineageTracker:
    """Tracks data lineage across pipeline stages."""

    def __init__(self, lineage_dir: Path = Path("data/lineage")):
        """Initialize lineage tracker.

        Args:
            lineage_dir: Directory to store lineage records
        """
        self.lineage_dir = Path(lineage_dir)
        self.lineage_dir.mkdir(parents=True, exist_ok=True)

        self.nodes: dict[str, LineageNode] = {}
        self.edges: list[LineageEdge] = []

    def track_discovery(
        self,
        url: str,
        url_hash: str,
        source_url: str | None = None,
        discovery_method: str = "html_link"
    ):
        """Track URL discovery in Stage 1.

        Args:
            url: Discovered URL
            url_hash: Hash of the URL
            source_url: URL where this was discovered
            discovery_method: Method of discovery
        """
        node = LineageNode(
            stage="stage1_discovery",
            url=url,
            url_hash=url_hash,
            timestamp=datetime.now().isoformat(),
            metadata={
                "source_url": source_url,
                "discovery_method": discovery_method
            }
        )
        self.nodes[url_hash] = node

        # Create edge from source if available
        if source_url:
            self.edges.append(LineageEdge(
                from_hash=self._hash_url(source_url),
                to_hash=url_hash,
                transformation="discovered_via",
                timestamp=node.timestamp
            ))

    def track_validation(
        self,
        url: str,
        url_hash: str,
        is_valid: bool,
        status_code: int
    ):
        """Track URL validation in Stage 2.

        Args:
            url: Validated URL
            url_hash: Hash of the URL
            is_valid: Whether URL is valid
            status_code: HTTP status code
        """
        # Get or create node
        node = self.nodes.get(url_hash)
        if node:
            # Update existing node
            node.metadata = node.metadata or {}
            node.metadata.update({
                "validated": True,
                "is_valid": is_valid,
                "status_code": status_code,
                "validation_timestamp": datetime.now().isoformat()
            })
        else:
            # Create new node
            node = LineageNode(
                stage="stage2_validation",
                url=url,
                url_hash=url_hash,
                timestamp=datetime.now().isoformat(),
                metadata={
                    "is_valid": is_valid,
                    "status_code": status_code
                }
            )
            self.nodes[url_hash] = node

        # Create edge showing validation flow
        self.edges.append(LineageEdge(
            from_hash=url_hash,
            to_hash=f"{url_hash}_validated",
            transformation="validated",
            timestamp=node.timestamp
        ))

    def track_enrichment(
        self,
        url: str,
        url_hash: str,
        word_count: int,
        entities_count: int,
        keywords_count: int
    ):
        """Track URL enrichment in Stage 3.

        Args:
            url: Enriched URL
            url_hash: Hash of the URL
            word_count: Number of words extracted
            entities_count: Number of entities found
            keywords_count: Number of keywords extracted
        """
        # Update existing node
        node = self.nodes.get(url_hash)
        if node:
            node.metadata = node.metadata or {}
            node.metadata.update({
                "enriched": True,
                "word_count": word_count,
                "entities_count": entities_count,
                "keywords_count": keywords_count,
                "enrichment_timestamp": datetime.now().isoformat()
            })
        else:
            # Create new node
            node = LineageNode(
                stage="stage3_enrichment",
                url=url,
                url_hash=url_hash,
                timestamp=datetime.now().isoformat(),
                metadata={
                    "word_count": word_count,
                    "entities_count": entities_count,
                    "keywords_count": keywords_count
                }
            )
            self.nodes[url_hash] = node

        # Create edge showing enrichment flow
        self.edges.append(LineageEdge(
            from_hash=f"{url_hash}_validated",
            to_hash=f"{url_hash}_enriched",
            transformation="enriched",
            timestamp=node.timestamp
        ))

    def save_lineage(self, filename: str = "lineage.json"):
        """Save lineage graph to file.

        Args:
            filename: Output filename
        """
        output_file = self.lineage_dir / filename

        lineage_data = {
            "nodes": [asdict(node) for node in self.nodes.values()],
            "edges": [asdict(edge) for edge in self.edges],
            "generated_at": datetime.now().isoformat(),
            "node_count": len(self.nodes),
            "edge_count": len(self.edges)
        }

        try:
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(lineage_data, f, indent=2, ensure_ascii=False)
            logger.info(f"Saved lineage to {output_file}")
        except Exception as e:
            logger.error(f"Failed to save lineage: {e}")

    def load_lineage(self, filename: str = "lineage.json"):
        """Load lineage graph from file.

        Args:
            filename: Input filename
        """
        input_file = self.lineage_dir / filename

        try:
            with open(input_file, encoding='utf-8') as f:
                lineage_data = json.load(f)

            # Reconstruct nodes
            self.nodes = {
                node['url_hash']: LineageNode(**node)
                for node in lineage_data['nodes']
            }

            # Reconstruct edges
            self.edges = [
                LineageEdge(**edge)
                for edge in lineage_data['edges']
            ]

            logger.info(f"Loaded lineage from {input_file}")
        except Exception as e:
            logger.error(f"Failed to load lineage: {e}")

    def get_lineage_path(self, url_hash: str) -> list[LineageNode]:
        """Get complete lineage path for a URL.

        Args:
            url_hash: Hash of the URL

        Returns:
            List of LineageNode objects representing the path
        """
        path = []
        current_hash = url_hash

        # Trace backwards through edges
        visited = set()
        while current_hash and current_hash not in visited:
            visited.add(current_hash)

            node = self.nodes.get(current_hash)
            if node:
                path.append(node)

            # Find predecessor
            predecessor = None
            for edge in self.edges:
                if edge.to_hash == current_hash:
                    predecessor = edge.from_hash
                    break

            current_hash = predecessor

        return list(reversed(path))

    def verify_lineage(self) -> dict[str, Any]:
        """Verify lineage integrity.

        Returns:
            Dictionary with verification results
        """
        results = {
            "total_nodes": len(self.nodes),
            "total_edges": len(self.edges),
            "orphaned_nodes": [],
            "broken_edges": [],
            "complete_paths": 0
        }

        # Find orphaned nodes (no incoming edges)
        nodes_with_incoming = set(edge.to_hash for edge in self.edges)
        for url_hash, node in self.nodes.items():
            if url_hash not in nodes_with_incoming and node.stage != "stage1_discovery":
                results["orphaned_nodes"].append(url_hash)

        # Find broken edges (referencing non-existent nodes)
        for edge in self.edges:
            if edge.from_hash not in self.nodes and not edge.from_hash.endswith("_validated"):
                results["broken_edges"].append(edge.from_hash)
            if edge.to_hash not in self.nodes and not edge.to_hash.startswith(tuple(self.nodes.keys())):
                results["broken_edges"].append(edge.to_hash)

        # Count complete paths (discovery → validation → enrichment)
        for node in self.nodes.values():
            if node.stage == "stage3_enrichment":
                path = self.get_lineage_path(node.url_hash)
                if len(path) >= 3:  # All three stages
                    results["complete_paths"] += 1

        return results

    def generate_stats(self) -> dict[str, Any]:
        """Generate lineage statistics.

        Returns:
            Dictionary with statistics
        """
        stats = {
            "total_urls": len(self.nodes),
            "by_stage": defaultdict(int),
            "discovery_methods": defaultdict(int),
            "valid_urls": 0,
            "enriched_urls": 0,
            "avg_word_count": 0,
        }

        word_counts = []

        for node in self.nodes.values():
            stats["by_stage"][node.stage] += 1

            if node.metadata:
                # Discovery method
                if "discovery_method" in node.metadata:
                    stats["discovery_methods"][node.metadata["discovery_method"]] += 1

                # Validation
                if node.metadata.get("is_valid"):
                    stats["valid_urls"] += 1

                # Enrichment
                if node.metadata.get("enriched"):
                    stats["enriched_urls"] += 1
                    if "word_count" in node.metadata:
                        word_counts.append(node.metadata["word_count"])

        # Calculate averages
        if word_counts:
            stats["avg_word_count"] = sum(word_counts) / len(word_counts)

        return dict(stats)

    @staticmethod
    def _hash_url(url: str) -> str:
        """Generate hash for URL (using same method as pipeline)."""
        import hashlib
        return hashlib.sha256(url.encode('utf-8')).hexdigest()


def build_lineage_from_files(
    stage1_file: Path,
    stage2_file: Path | None = None,
    stage3_file: Path | None = None
) -> LineageTracker:
    """Build lineage tracker from pipeline output files.

    Args:
        stage1_file: Stage 1 discovery output
        stage2_file: Stage 2 validation output (optional)
        stage3_file: Stage 3 enrichment output (optional)

    Returns:
        LineageTracker with populated lineage
    """
    tracker = LineageTracker()

    # Process Stage 1
    if stage1_file and stage1_file.exists():
        logger.info(f"Processing Stage 1 file: {stage1_file}")
        with open(stage1_file, encoding='utf-8') as f:
            for line in f:
                try:
                    data = json.loads(line.strip())
                    tracker.track_discovery(
                        url=data.get('discovered_url', ''),
                        url_hash=data.get('url_hash', ''),
                        source_url=data.get('source_url'),
                        discovery_method=data.get('discovery_source', 'html_link')
                    )
                except json.JSONDecodeError:
                    continue

    # Process Stage 2
    if stage2_file and stage2_file.exists():
        logger.info(f"Processing Stage 2 file: {stage2_file}")
        with open(stage2_file, encoding='utf-8') as f:
            for line in f:
                try:
                    data = json.loads(line.strip())
                    tracker.track_validation(
                        url=data.get('url', ''),
                        url_hash=data.get('url_hash', ''),
                        is_valid=data.get('is_valid', False),
                        status_code=data.get('status_code', 0)
                    )
                except json.JSONDecodeError:
                    continue

    # Process Stage 3
    if stage3_file and stage3_file.exists():
        logger.info(f"Processing Stage 3 file: {stage3_file}")
        with open(stage3_file, encoding='utf-8') as f:
            for line in f:
                try:
                    data = json.loads(line.strip())
                    tracker.track_enrichment(
                        url=data.get('url', ''),
                        url_hash=data.get('url_hash', ''),
                        word_count=data.get('word_count', 0),
                        entities_count=len(data.get('entities', [])),
                        keywords_count=len(data.get('keywords', []))
                    )
                except json.JSONDecodeError:
                    continue

    logger.info(f"Built lineage with {len(tracker.nodes)} nodes and {len(tracker.edges)} edges")
    return tracker


def generate_lineage_report(tracker: LineageTracker, output_file: Path):
    """Generate human-readable lineage report.

    Args:
        tracker: LineageTracker instance
        output_file: Output file path
    """
    stats = tracker.generate_stats()
    verification = tracker.verify_lineage()

    report = [
        "=" * 80,
        "DATA LINEAGE REPORT",
        "=" * 80,
        "",
        f"Generated: {datetime.now().isoformat()}",
        "",
        "STATISTICS:",
        f"  Total URLs tracked: {stats['total_urls']}",
        "  By stage:",
    ]

    for stage, count in sorted(stats['by_stage'].items()):
        report.append(f"    {stage}: {count}")

    report.extend([
        "",
        "  Discovery methods:",
    ])

    for method, count in sorted(stats['discovery_methods'].items()):
        report.append(f"    {method}: {count}")

    report.extend([
        "",
        f"  Valid URLs: {stats['valid_urls']}",
        f"  Enriched URLs: {stats['enriched_urls']}",
        f"  Average word count: {stats['avg_word_count']:.0f}",
        "",
        "VERIFICATION:",
        f"  Total nodes: {verification['total_nodes']}",
        f"  Total edges: {verification['total_edges']}",
        f"  Complete paths: {verification['complete_paths']}",
        f"  Orphaned nodes: {len(verification['orphaned_nodes'])}",
        f"  Broken edges: {len(verification['broken_edges'])}",
        "",
        "=" * 80,
    ])

    report_text = "\n".join(report)

    # Write to file
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(report_text)

    logger.info(f"Generated lineage report: {output_file}")
    return report_text
