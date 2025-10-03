"""
Link Graph Analysis Module

Provides PageRank-style importance scoring, link graph construction,
and network analysis for discovered URLs.
"""

import json
import logging
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


@dataclass
class PageImportance:
    """Page importance metrics"""
    url: str
    pagerank_score: float = 0.0
    inlink_count: int = 0
    outlink_count: int = 0
    hub_score: float = 0.0  # HITS algorithm hub score
    authority_score: float = 0.0  # HITS algorithm authority score

    # Centrality metrics
    degree_centrality: float = 0.0
    betweenness_centrality: float = 0.0

    # Content importance boosters
    is_homepage: bool = False
    depth_from_root: int = 999
    domain: str = ""


@dataclass
class LinkGraphStats:
    """Link graph statistics"""
    total_nodes: int = 0
    total_edges: int = 0
    avg_degree: float = 0.0
    max_degree: int = 0

    # Community detection
    num_communities: int = 0
    largest_community_size: int = 0

    # Top pages
    top_pages_by_pagerank: list[tuple[str, float]] = field(default_factory=list)
    top_hubs: list[tuple[str, float]] = field(default_factory=list)
    top_authorities: list[tuple[str, float]] = field(default_factory=list)


class LinkGraphAnalyzer:
    """
    Analyze link graph and calculate importance scores.

    Implements PageRank, HITS algorithm, and network centrality metrics.
    """

    def __init__(self, db_path: Path | None = None):
        """
        Initialize link graph analyzer.

        Args:
            db_path: Path to SQLite database for persistent storage
        """
        self.db_path = db_path or Path("data/processed/link_graph.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # In-memory graph structures
        self.adjacency_list: dict[str, set[str]] = defaultdict(set)  # URL -> outlinks
        self.reverse_adjacency_list: dict[str, set[str]] = defaultdict(set)  # URL -> inlinks
        self.url_metadata: dict[str, dict] = {}

        # Initialize database
        self._init_db()

    def _init_db(self):
        """Initialize SQLite database schema"""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS nodes (
                    url TEXT PRIMARY KEY,
                    domain TEXT,
                    depth_from_root INTEGER,
                    is_homepage BOOLEAN,
                    first_seen TEXT,
                    metadata TEXT
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS edges (
                    source_url TEXT,
                    target_url TEXT,
                    first_seen TEXT,
                    PRIMARY KEY (source_url, target_url)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS importance_scores (
                    url TEXT PRIMARY KEY,
                    pagerank_score REAL,
                    hub_score REAL,
                    authority_score REAL,
                    degree_centrality REAL,
                    inlink_count INTEGER,
                    outlink_count INTEGER,
                    last_updated TEXT
                )
            """)

            conn.commit()

    def add_page(
        self,
        url: str,
        outlinks: list[str],
        depth: int = 0,
        metadata: dict | None = None
    ):
        """
        Add a page and its outlinks to the graph.

        Args:
            url: Source page URL
            outlinks: List of URLs linked from this page
            depth: Depth from root/homepage
            metadata: Optional metadata about the page
        """
        parsed = urlparse(url)
        domain = parsed.netloc
        is_homepage = parsed.path in ['', '/']

        # Add to in-memory graph
        for outlink in outlinks:
            self.adjacency_list[url].add(outlink)
            self.reverse_adjacency_list[outlink].add(url)

        # Store metadata
        self.url_metadata[url] = {
            'domain': domain,
            'depth': depth,
            'is_homepage': is_homepage,
            'metadata': metadata or {}
        }

        # Persist to database
        with sqlite3.connect(str(self.db_path)) as conn:
            from datetime import datetime
            now = datetime.now().isoformat()

            # Insert node
            conn.execute("""
                INSERT OR REPLACE INTO nodes
                (url, domain, depth_from_root, is_homepage, first_seen, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                url,
                domain,
                depth,
                is_homepage,
                now,
                json.dumps(metadata or {})
            ))

            # Insert edges
            for outlink in outlinks:
                conn.execute("""
                    INSERT OR IGNORE INTO edges
                    (source_url, target_url, first_seen)
                    VALUES (?, ?, ?)
                """, (url, outlink, now))

            conn.commit()

    def calculate_pagerank(
        self,
        damping_factor: float = 0.85,
        max_iterations: int = 100,
        convergence_threshold: float = 0.0001
    ) -> dict[str, float]:
        """
        Calculate PageRank scores for all pages in the graph.

        Args:
            damping_factor: Probability of following a link (default: 0.85)
            max_iterations: Maximum iterations (default: 100)
            convergence_threshold: Convergence threshold (default: 0.0001)

        Returns:
            Dict mapping URL to PageRank score
        """
        # Get all nodes
        all_nodes = set(self.adjacency_list.keys()) | set(self.reverse_adjacency_list.keys())
        num_nodes = len(all_nodes)

        if num_nodes == 0:
            return {}

        # Initialize PageRank scores
        pagerank = {url: 1.0 / num_nodes for url in all_nodes}
        new_pagerank = pagerank.copy()

        # Iterative calculation
        for iteration in range(max_iterations):
            max_diff = 0.0

            for url in all_nodes:
                # Get inlinks
                inlinks = self.reverse_adjacency_list.get(url, set())

                # Calculate new PageRank
                rank_sum = 0.0
                for inlink in inlinks:
                    outlink_count = len(self.adjacency_list.get(inlink, set()))
                    if outlink_count > 0:
                        rank_sum += pagerank[inlink] / outlink_count

                new_rank = (1 - damping_factor) / num_nodes + damping_factor * rank_sum
                new_pagerank[url] = new_rank

                # Track convergence
                diff = abs(new_rank - pagerank[url])
                max_diff = max(max_diff, diff)

            # Check convergence
            if max_diff < convergence_threshold:
                logger.info(f"PageRank converged after {iteration + 1} iterations")
                break

            pagerank = new_pagerank.copy()

        # Normalize scores to 0-1 range
        max_score = max(pagerank.values()) if pagerank else 1.0
        if max_score > 0:
            pagerank = {url: score / max_score for url, score in pagerank.items()}

        # Persist scores
        self._save_pagerank_scores(pagerank)

        return pagerank

    def calculate_hits(
        self,
        max_iterations: int = 100,
        convergence_threshold: float = 0.0001
    ) -> tuple[dict[str, float], dict[str, float]]:
        """
        Calculate HITS (Hyperlink-Induced Topic Search) algorithm scores.

        Returns hub and authority scores.

        Args:
            max_iterations: Maximum iterations (default: 100)
            convergence_threshold: Convergence threshold (default: 0.0001)

        Returns:
            Tuple of (hub_scores, authority_scores)
        """
        all_nodes = set(self.adjacency_list.keys()) | set(self.reverse_adjacency_list.keys())

        if not all_nodes:
            return {}, {}

        # Initialize scores
        hub_scores = {url: 1.0 for url in all_nodes}
        authority_scores = {url: 1.0 for url in all_nodes}

        # Iterative calculation
        for iteration in range(max_iterations):
            new_hub_scores = {}
            new_authority_scores = {}

            # Update authority scores
            for url in all_nodes:
                inlinks = self.reverse_adjacency_list.get(url, set())
                new_authority_scores[url] = sum(hub_scores.get(inlink, 0) for inlink in inlinks)

            # Update hub scores
            for url in all_nodes:
                outlinks = self.adjacency_list.get(url, set())
                new_hub_scores[url] = sum(authority_scores.get(outlink, 0) for outlink in outlinks)

            # Normalize scores
            hub_norm = sum(score ** 2 for score in new_hub_scores.values()) ** 0.5
            auth_norm = sum(score ** 2 for score in new_authority_scores.values()) ** 0.5

            if hub_norm > 0:
                new_hub_scores = {url: score / hub_norm for url, score in new_hub_scores.items()}
            if auth_norm > 0:
                new_authority_scores = {url: score / auth_norm for url, score in new_authority_scores.items()}

            # Check convergence
            hub_diff = max(abs(new_hub_scores[url] - hub_scores[url]) for url in all_nodes)
            auth_diff = max(abs(new_authority_scores[url] - authority_scores[url]) for url in all_nodes)

            if max(hub_diff, auth_diff) < convergence_threshold:
                logger.info(f"HITS converged after {iteration + 1} iterations")
                break

            hub_scores = new_hub_scores
            authority_scores = new_authority_scores

        # Persist scores
        self._save_hits_scores(hub_scores, authority_scores)

        return hub_scores, authority_scores

    def get_page_importance(self, url: str) -> PageImportance:
        """
        Get comprehensive importance metrics for a page.

        Args:
            url: Page URL

        Returns:
            PageImportance with all metrics
        """
        # Load from database if available
        with sqlite3.connect(str(self.db_path)) as conn:
            cursor = conn.execute("""
                SELECT pagerank_score, hub_score, authority_score,
                       degree_centrality, inlink_count, outlink_count
                FROM importance_scores
                WHERE url = ?
            """, (url,))

            row = cursor.fetchone()

            if row:
                pagerank_score, hub_score, authority_score, degree_centrality, inlink_count, outlink_count = row
            else:
                # Calculate on-the-fly
                pagerank_score = 0.0
                hub_score = 0.0
                authority_score = 0.0
                degree_centrality = 0.0
                inlink_count = len(self.reverse_adjacency_list.get(url, set()))
                outlink_count = len(self.adjacency_list.get(url, set()))

        # Get metadata
        metadata = self.url_metadata.get(url, {})
        domain = metadata.get('domain', urlparse(url).netloc)
        depth = metadata.get('depth', 999)
        is_homepage = metadata.get('is_homepage', False)

        return PageImportance(
            url=url,
            pagerank_score=pagerank_score,
            inlink_count=inlink_count,
            outlink_count=outlink_count,
            hub_score=hub_score,
            authority_score=authority_score,
            degree_centrality=degree_centrality,
            is_homepage=is_homepage,
            depth_from_root=depth,
            domain=domain
        )

    def get_top_pages(
        self,
        metric: str = "pagerank",
        limit: int = 100
    ) -> list[tuple[str, float]]:
        """
        Get top pages by importance metric.

        Args:
            metric: Importance metric (pagerank, hub, authority, degree)
            limit: Number of pages to return

        Returns:
            List of (url, score) tuples sorted by score
        """
        metric_column = {
            'pagerank': 'pagerank_score',
            'hub': 'hub_score',
            'authority': 'authority_score',
            'degree': 'degree_centrality'
        }.get(metric, 'pagerank_score')

        with sqlite3.connect(str(self.db_path)) as conn:
            cursor = conn.execute(f"""
                SELECT url, {metric_column}
                FROM importance_scores
                WHERE {metric_column} IS NOT NULL
                ORDER BY {metric_column} DESC
                LIMIT ?
            """, (limit,))

            return [(url, score) for url, score in cursor.fetchall()]

    def get_graph_stats(self) -> LinkGraphStats:
        """
        Get comprehensive graph statistics.

        Returns:
            LinkGraphStats with network metrics
        """
        all_nodes = set(self.adjacency_list.keys()) | set(self.reverse_adjacency_list.keys())
        total_nodes = len(all_nodes)

        # Count edges
        total_edges = sum(len(outlinks) for outlinks in self.adjacency_list.values())

        # Calculate average degree
        degrees = [len(self.adjacency_list.get(url, set())) + len(self.reverse_adjacency_list.get(url, set()))
                  for url in all_nodes]
        avg_degree = sum(degrees) / len(degrees) if degrees else 0
        max_degree = max(degrees) if degrees else 0

        # Get top pages
        top_pagerank = self.get_top_pages('pagerank', 10)
        top_hubs = self.get_top_pages('hub', 10)
        top_authorities = self.get_top_pages('authority', 10)

        return LinkGraphStats(
            total_nodes=total_nodes,
            total_edges=total_edges,
            avg_degree=round(avg_degree, 2),
            max_degree=max_degree,
            top_pages_by_pagerank=top_pagerank,
            top_hubs=top_hubs,
            top_authorities=top_authorities
        )

    def load_from_discovery_data(self, discovery_db_path: Path):
        """
        Load link graph from discovery stage database.

        Args:
            discovery_db_path: Path to discovery database
        """
        if not discovery_db_path.exists():
            logger.warning(f"Discovery database not found: {discovery_db_path}")
            return

        with sqlite3.connect(str(discovery_db_path)) as conn:
            # Load all discovered URLs and their sources
            cursor = conn.execute("""
                SELECT source_url, discovered_url, discovery_depth
                FROM discovered_urls
                ORDER BY discovery_depth
            """)

            page_outlinks = defaultdict(list)

            for source_url, discovered_url, depth in cursor.fetchall():
                page_outlinks[source_url].append(discovered_url)

            # Add pages to graph
            for source_url, outlinks in page_outlinks.items():
                # Infer depth (this is approximate)
                depth = 0  # Could be improved with actual depth tracking
                self.add_page(source_url, outlinks, depth=depth)

        logger.info(f"Loaded {len(page_outlinks)} pages from discovery database")

    def _save_pagerank_scores(self, pagerank: dict[str, float]):
        """Persist PageRank scores to database"""
        from datetime import datetime
        now = datetime.now().isoformat()

        with sqlite3.connect(str(self.db_path)) as conn:
            for url, score in pagerank.items():
                inlink_count = len(self.reverse_adjacency_list.get(url, set()))
                outlink_count = len(self.adjacency_list.get(url, set()))

                conn.execute("""
                    INSERT OR REPLACE INTO importance_scores
                    (url, pagerank_score, inlink_count, outlink_count, last_updated)
                    VALUES (?, ?, ?, ?, ?)
                """, (url, score, inlink_count, outlink_count, now))

            conn.commit()

    def _save_hits_scores(self, hub_scores: dict[str, float], authority_scores: dict[str, float]):
        """Persist HITS scores to database"""
        from datetime import datetime
        now = datetime.now().isoformat()

        with sqlite3.connect(str(self.db_path)) as conn:
            for url in hub_scores:
                conn.execute("""
                    INSERT OR REPLACE INTO importance_scores
                    (url, hub_score, authority_score, last_updated)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(url) DO UPDATE SET
                        hub_score = excluded.hub_score,
                        authority_score = excluded.authority_score,
                        last_updated = excluded.last_updated
                """, (url, hub_scores[url], authority_scores[url], now))

            conn.commit()
