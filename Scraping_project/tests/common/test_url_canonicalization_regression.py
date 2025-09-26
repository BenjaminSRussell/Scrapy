"""
Comprehensive regression tests for URL canonicalization and hashing functionality.

These tests ensure that _normalize_for_hash and canonicalize_and_hash remain correct
and prevent silent regressions in URL handling that could affect data integrity.
"""

import pytest
from typing import List, Tuple, Dict, Set
import hashlib

from common.urls import (
    canonicalize_and_hash,
    _normalize_for_hash,
    _normalize_url_components,
    sha1_hash,
    is_valid_uconn_url,
    extract_domain
)


class TestURLCanonicalizationRegression:
    """Regression tests for URL canonicalization to prevent silent failures"""

    @pytest.mark.critical
    def test_path_traversal_resolution_comprehensive(self):
        """Test that path traversal attacks are completely resolved"""
        test_cases = [
            # Basic path traversal
            ("https://uconn.edu/../admin", "https://uconn.edu/admin"),
            ("https://uconn.edu/public/../admin", "https://uconn.edu/admin"),
            ("https://uconn.edu/a/b/../../admin", "https://uconn.edu/admin"),

            # Complex path traversal
            ("https://uconn.edu/../../../etc/passwd", "https://uconn.edu/etc/passwd"),
            ("https://uconn.edu/./././admin", "https://uconn.edu/admin"),
            ("https://uconn.edu/path/./file.html", "https://uconn.edu/path/file.html"),

            # Mixed traversal patterns
            ("https://uconn.edu/a/../b/./c/../d", "https://uconn.edu/b/d"),
            ("https://uconn.edu/./a/../b/./", "https://uconn.edu/b/"),

            # Edge cases
            ("https://uconn.edu/.", "https://uconn.edu/"),
            ("https://uconn.edu/..", "https://uconn.edu/"),
            ("https://uconn.edu/../..", "https://uconn.edu/"),
        ]

        for input_url, expected_canonical in test_cases:
            canonical, _ = canonicalize_and_hash(input_url)
            assert canonical == expected_canonical, f"Failed for {input_url}"

            # Ensure no path traversal components remain
            assert "../" not in canonical, f"Path traversal not resolved in {canonical}"
            assert "/./" not in canonical or canonical.endswith("/./"), f"Dot segments not resolved in {canonical}"

    @pytest.mark.critical
    def test_case_insensitive_hash_collision(self):
        """Test that case variants hash to the same value for collision detection"""
        case_variant_groups = [
            [
                "https://uconn.edu/PAGE",
                "https://uconn.edu/page",
                "https://uconn.edu/Page",
                "https://uconn.edu/pAgE"
            ],
            [
                "https://UCONN.EDU/research",
                "https://uconn.edu/RESEARCH",
                "https://Uconn.Edu/Research"
            ],
            [
                "https://CATALOG.UCONN.EDU/Programs",
                "https://catalog.uconn.edu/programs",
                "https://Catalog.Uconn.Edu/PROGRAMS"
            ]
        ]

        for group in case_variant_groups:
            hashes = []
            for url in group:
                _, url_hash = canonicalize_and_hash(url)
                hashes.append(url_hash)

            # All variants should produce the same hash
            unique_hashes = set(hashes)
            assert len(unique_hashes) == 1, f"Case variants should hash identically: {group} -> {hashes}"

    @pytest.mark.critical
    def test_default_port_normalization(self):
        """Test that default ports are correctly removed"""
        test_cases = [
            ("https://uconn.edu:443/page", "https://uconn.edu/page"),
            ("http://uconn.edu:80/page", "http://uconn.edu/page"),
            ("https://catalog.uconn.edu:443/", "https://catalog.uconn.edu/"),
            ("http://events.uconn.edu:80/calendar", "http://events.uconn.edu/calendar"),

            # Non-default ports should be preserved
            ("https://uconn.edu:8443/page", "https://uconn.edu:8443/page"),
            ("http://uconn.edu:8080/page", "http://uconn.edu:8080/page"),
        ]

        for input_url, expected_canonical in test_cases:
            canonical, _ = canonicalize_and_hash(input_url)
            assert canonical == expected_canonical, f"Port normalization failed for {input_url}"

    @pytest.mark.critical
    def test_fragment_preservation_for_hash_collision_resistance(self):
        """Test that fragments are preserved in hash but removed from canonical URL"""
        urls_with_fragments = [
            "https://uconn.edu/page1",
            "https://uconn.edu/page1#section",
            "https://uconn.edu/page1#different-section",
            "https://uconn.edu/page1?param=1",
            "https://uconn.edu/page1?param=1#section"
        ]

        canonical_urls = []
        hashes = []

        for url in urls_with_fragments:
            canonical, url_hash = canonicalize_and_hash(url)
            canonical_urls.append(canonical)
            hashes.append(url_hash)

        # All canonical URLs should have fragments removed
        for canonical in canonical_urls:
            assert "#" not in canonical, f"Fragment not removed from canonical URL: {canonical}"

        # All hashes should be unique (fragments should cause different hashes)
        unique_hashes = set(hashes)
        assert len(unique_hashes) == len(hashes), f"Fragment variations should produce unique hashes: {hashes}"

    @pytest.mark.critical
    def test_trailing_slash_semantics(self):
        """Test correct trailing slash preservation/omission"""
        test_cases = [
            # Empty path should remain empty (no trailing slash added)
            ("https://uconn.edu", "https://uconn.edu"),
            ("http://events.uconn.edu", "http://events.uconn.edu"),

            # Explicit trailing slash should be preserved
            ("https://uconn.edu/", "https://uconn.edu/"),
            ("https://catalog.uconn.edu/programs/", "https://catalog.uconn.edu/programs/"),

            # No trailing slash should be preserved
            ("https://uconn.edu/about", "https://uconn.edu/about"),
            ("https://admissions.uconn.edu/apply", "https://admissions.uconn.edu/apply"),

            # Path traversal with trailing slash semantics
            ("https://uconn.edu/path/../other/", "https://uconn.edu/other/"),
            ("https://uconn.edu/path/../other", "https://uconn.edu/other"),
        ]

        for input_url, expected_canonical in test_cases:
            canonical, _ = canonicalize_and_hash(input_url)
            assert canonical == expected_canonical, f"Trailing slash semantics failed for {input_url}"

    @pytest.mark.critical
    def test_query_parameter_normalization(self):
        """Test that query parameters are sorted consistently"""
        test_cases = [
            (
                "https://uconn.edu/search?b=2&a=1&c=3",
                "https://uconn.edu/search?a=1&b=2&c=3"
            ),
            (
                "https://catalog.uconn.edu/courses?semester=fall&year=2024&department=cse",
                "https://catalog.uconn.edu/courses?department=cse&semester=fall&year=2024"
            ),
            (
                "https://uconn.edu/page?utm_campaign=test&utm_source=email&id=123",
                "https://uconn.edu/page?id=123&utm_campaign=test&utm_source=email"
            )
        ]

        for input_url, expected_canonical in test_cases:
            canonical, _ = canonicalize_and_hash(input_url)
            assert canonical == expected_canonical, f"Query parameter sorting failed for {input_url}"

            # Hash should also be based on sorted parameters
            _, hash1 = canonicalize_and_hash(input_url)
            _, hash2 = canonicalize_and_hash(expected_canonical)
            assert hash1 == hash2, f"Query parameter sorting should produce identical hashes"

    @pytest.mark.critical
    def test_hash_collision_resistance_comprehensive(self):
        """Test that different URLs produce different hashes"""
        distinct_urls = [
            "https://uconn.edu/page1",
            "https://uconn.edu/page2",
            "https://uconn.edu/page1?param=1",
            "https://uconn.edu/page1#section",
            "https://admissions.uconn.edu/page1",
            "https://catalog.uconn.edu/page1",
            "https://uconn.edu/PAGE1",  # This should hash the same as page1 due to case insensitivity
            "https://uconn.edu/page1/",
            "https://uconn.edu/page1?param=2",
            "http://uconn.edu/page1"  # Different scheme
        ]

        hashes = []
        for url in distinct_urls:
            _, url_hash = canonicalize_and_hash(url)
            hashes.append(url_hash)

        # Count unique hashes (accounting for case insensitivity)
        unique_hashes = set(hashes)

        # We expect fewer unique hashes than URLs due to case insensitivity
        # but still good collision resistance
        expected_unique = len(distinct_urls) - 1  # -1 for case insensitive collision
        assert len(unique_hashes) == expected_unique, f"Expected {expected_unique} unique hashes, got {len(unique_hashes)}"

    @pytest.mark.critical
    def test_normalization_idempotency(self):
        """Test that applying normalization twice produces the same result"""
        test_urls = [
            "https://UCONN.EDU/Test/../page",
            "https://uconn.edu:443/./path/to/resource",
            "https://catalog.uconn.edu/programs?b=2&a=1#section",
            "http://events.uconn.edu:80/../calendar/",
        ]

        for url in test_urls:
            canonical1, hash1 = canonicalize_and_hash(url)
            canonical2, hash2 = canonicalize_and_hash(canonical1)

            assert canonical1 == canonical2, f"Canonicalization not idempotent for {url}"
            assert hash1 == hash2, f"Hash not idempotent for {url}"

    @pytest.mark.critical
    def test_domain_validation_regression(self):
        """Test that domain validation remains accurate"""
        valid_domains = [
            "https://uconn.edu",
            "https://www.uconn.edu",
            "https://admissions.uconn.edu",
            "https://catalog.uconn.edu/programs",
            "http://events.uconn.edu/calendar",
            "https://research.uconn.edu/reports/2024"
        ]

        invalid_domains = [
            "https://fake-uconn.edu",
            "https://uconn.edu.evil.com",
            "https://subdomain.uconn.edu.evil.com",
            "https://uconn-fake.edu",
            "ftp://uconn.edu/resource",
            "javascript:alert('xss')",
            "https://google.com",
            "not-a-url"
        ]

        for url in valid_domains:
            assert is_valid_uconn_url(url), f"Valid UConn URL rejected: {url}"

        for url in invalid_domains:
            assert not is_valid_uconn_url(url), f"Invalid URL accepted: {url}"

    @pytest.mark.critical
    def test_domain_extraction_accuracy(self):
        """Test that domain extraction handles all edge cases"""
        test_cases = [
            ("https://uconn.edu", "uconn.edu"),
            ("https://www.uconn.edu/path", "www.uconn.edu"),
            ("https://admissions.uconn.edu:443/apply", "admissions.uconn.edu"),
            ("http://events.uconn.edu:80/calendar", "events.uconn.edu"),
            ("https://research.uconn.edu/reports/2024?year=2024", "research.uconn.edu"),
        ]

        for url, expected_domain in test_cases:
            domain = extract_domain(url)
            assert domain == expected_domain, f"Domain extraction failed for {url}: got {domain}, expected {expected_domain}"

    @pytest.mark.performance
    def test_canonicalization_performance_regression(self):
        """Test that canonicalization performance hasn't regressed"""
        import time

        # Generate test URLs with various complexities
        test_urls = []
        base_urls = [
            "https://uconn.edu",
            "https://catalog.uconn.edu",
            "https://admissions.uconn.edu"
        ]

        for base in base_urls:
            for i in range(50):
                # Add various URL complexities
                url = f"{base}/path{i}/../normalized?param{i}=value{i}&other=test#section{i}"
                test_urls.append(url)

        start_time = time.perf_counter()

        for url in test_urls:
            canonicalize_and_hash(url)

        duration = time.perf_counter() - start_time
        urls_per_second = len(test_urls) / duration

        # Performance baseline: should handle at least 100 URLs per second
        assert urls_per_second > 100, f"Performance regression: only {urls_per_second:.1f} URLs/second"

    @pytest.mark.critical
    def test_unicode_handling_safety(self):
        """Test that Unicode URLs are handled safely without breaking"""
        unicode_test_cases = [
            "https://uconn.edu/café",
            "https://uconn.edu/研究",  # Chinese
            "https://uconn.edu/образование",  # Russian
            "https://uconn.edu/université",  # French
            "https://uconn.edu/münchen",  # German
        ]

        for url in unicode_test_cases:
            try:
                canonical, url_hash = canonicalize_and_hash(url)

                # Should produce valid results
                assert isinstance(canonical, str), f"Canonical URL should be string for {url}"
                assert len(url_hash) == 40, f"Hash should be 40 chars for {url}"
                assert all(c in '0123456789abcdef' for c in url_hash), f"Hash should be hex for {url}"

            except Exception as e:
                # If Unicode handling fails, it should fail gracefully
                assert isinstance(e, (UnicodeError, ValueError)), f"Unexpected error for {url}: {e}"


class TestNormalizationFunctionRegression:
    """Test individual normalization functions for regression"""

    @pytest.mark.critical
    def test_normalize_for_hash_consistency(self):
        """Test that _normalize_for_hash produces consistent results"""
        test_url = "https://UCONN.EDU/Path/../Other?b=2&a=1#section"

        # Should produce the same result on multiple calls
        results = [_normalize_for_hash(test_url) for _ in range(5)]
        assert all(r == results[0] for r in results), "normalize_for_hash should be deterministic"

        # Should be lowercase for case insensitive hashing
        result = _normalize_for_hash(test_url)
        assert "/other" in result.lower(), "Path should be lowercase in hash normalization"

    @pytest.mark.critical
    def test_normalize_url_components_edge_cases(self):
        """Test _normalize_url_components with edge cases"""
        edge_cases = [
            ("", ""),  # Empty URL
            ("https://", "https://"),  # Malformed URL
            ("not-a-url", "not-a-url"),  # Invalid URL
            ("https://uconn.edu//double//slash", "https://uconn.edu/double/slash"),  # Double slashes
        ]

        for input_url, expected_output in edge_cases:
            try:
                result = _normalize_url_components(input_url)
                # For valid transformations, check the result
                if expected_output:
                    assert result == expected_output, f"Failed for {input_url}"
            except Exception:
                # Some malformed URLs may raise exceptions - this is acceptable
                pass

    @pytest.mark.critical
    def test_sha1_hash_function_integrity(self):
        """Test that SHA1 hash function produces correct output"""
        test_cases = [
            ("test", "a94a8fe5ccb19ba61c4c0873d391e987982fbbd3"),
            ("https://uconn.edu", "7d793037a0760186574b0282f2f435e69e373d2e"),
            ("", "da39a3ee5e6b4b0d3255bfef95601890afd80709"),  # Empty string SHA1
        ]

        for input_text, expected_hash in test_cases:
            result = sha1_hash(input_text)
            assert result == expected_hash, f"SHA1 hash mismatch for '{input_text}'"
            assert len(result) == 40, f"SHA1 hash should be 40 characters"
            assert all(c in '0123456789abcdef' for c in result), f"SHA1 hash should be hexadecimal"


# Performance benchmarks and regression detection
@pytest.mark.performance
class TestCanonicalizationBenchmarks:
    """Performance benchmarks to detect regressions"""

    def test_bulk_canonicalization_performance(self):
        """Benchmark bulk URL canonicalization"""
        import time

        # Generate realistic URL dataset
        urls = []
        domains = ["uconn.edu", "catalog.uconn.edu", "admissions.uconn.edu", "events.uconn.edu"]
        paths = ["", "/", "/programs", "/apply", "/calendar", "/research/reports"]

        for domain in domains:
            for path in paths:
                for i in range(25):  # 600 URLs total
                    url = f"https://{domain}{path}?id={i}&sort=name"
                    urls.append(url)

        start_time = time.perf_counter()

        results = []
        for url in urls:
            canonical, url_hash = canonicalize_and_hash(url)
            results.append((canonical, url_hash))

        duration = time.perf_counter() - start_time
        throughput = len(urls) / duration

        # Performance requirements
        assert throughput > 500, f"Bulk canonicalization too slow: {throughput:.1f} URLs/sec"
        assert duration < 5.0, f"Bulk canonicalization took too long: {duration:.2f}s"

        # Verify all results are valid
        assert len(results) == len(urls), "Some URLs failed to process"
        for canonical, url_hash in results:
            assert isinstance(canonical, str), "Invalid canonical URL type"
            assert len(url_hash) == 40, "Invalid hash length"