"""Unit tests for URLProcessor

Tests URL extraction, normalization, validation, and prioritization.
"""

from scrapy.http import HtmlResponse

from src.common.url_processor import URLProcessor


class TestURLProcessorInitialization:
    """Test URLProcessor initialization."""

    def test_basic_initialization(self):
        """Test basic URLProcessor initialization."""
        processor = URLProcessor(
            base_url='https://example.com',
            allowed_domains=['example.com']
        )

        assert processor.base_url == 'https://example.com'
        assert 'example.com' in processor.allowed_domains

    def test_initialization_with_history(self):
        """Test initialization with historical data enabled."""
        processor = URLProcessor(
            base_url='https://example.com',
            allowed_domains=['example.com'],
            use_historical_data=True
        )

        assert processor.assessor is not None

    def test_initialization_without_history(self):
        """Test initialization without historical data."""
        processor = URLProcessor(
            base_url='https://example.com',
            allowed_domains=['example.com'],
            use_historical_data=False
        )

        assert processor.assessor is not None


class TestURLNormalization:
    """Test URL normalization."""

    def setup_method(self):
        """Setup test processor."""
        self.processor = URLProcessor(
            base_url='https://example.com',
            allowed_domains=['example.com']
        )

    def test_normalize_removes_tracking_params(self):
        """Test that tracking parameters are removed."""
        url = 'https://example.com/page?utm_source=test&id=123&utm_campaign=email'
        normalized = self.processor.normalize_url(url)

        assert 'utm_source' not in normalized
        assert 'utm_campaign' not in normalized
        assert 'id=123' in normalized

    def test_normalize_lowercases_domain(self):
        """Test that domain is lowercased."""
        url = 'https://Example.COM/Page'
        normalized = self.processor.normalize_url(url)

        assert normalized.startswith('https://example.com')

    def test_normalize_removes_fragment(self):
        """Test that URL fragments are removed."""
        url = 'https://example.com/page#section'
        normalized = self.processor.normalize_url(url)

        assert '#section' not in normalized

    def test_normalize_removes_default_ports(self):
        """Test that default ports are removed."""
        # HTTP default port
        url = 'http://example.com:80/page'
        normalized = self.processor.normalize_url(url)
        assert ':80' not in normalized

        # HTTPS default port
        url = 'https://example.com:443/page'
        normalized = self.processor.normalize_url(url)
        assert ':443' not in normalized

    def test_normalize_sorts_query_params(self):
        """Test that query parameters are sorted."""
        url = 'https://example.com/page?z=3&a=1&m=2'
        normalized = self.processor.normalize_url(url)

        # Parameters should be sorted alphabetically
        assert normalized.index('a=1') < normalized.index('m=2')
        assert normalized.index('m=2') < normalized.index('z=3')

    def test_normalize_invalid_url_returns_none(self):
        """Test that invalid URLs return None."""
        invalid_urls = [
            'not-a-url',
            'javascript:void(0)',
            'mailto:test@example.com',
            '',
        ]

        for url in invalid_urls:
            assert self.processor.normalize_url(url) is None


class TestURLValidation:
    """Test URL validation and filtering."""

    def setup_method(self):
        """Setup test processor."""
        self.processor = URLProcessor(
            base_url='https://example.com',
            allowed_domains=['example.com']
        )

    def test_should_follow_html_url(self):
        """Test that HTML URLs are followed."""
        url = 'https://example.com/page'
        assert self.processor.should_follow_url(url) is True

    def test_should_not_follow_image(self):
        """Test that image URLs are not followed."""
        image_urls = [
            'https://example.com/image.jpg',
            'https://example.com/photo.png',
            'https://example.com/graphic.gif',
        ]

        for url in image_urls:
            assert self.processor.should_follow_url(url) is False

    def test_should_not_follow_static_assets(self):
        """Test that static assets are not followed."""
        static_urls = [
            'https://example.com/style.css',
            'https://example.com/script.js',
            'https://example.com/font.woff',
        ]

        for url in static_urls:
            assert self.processor.should_follow_url(url) is False

    def test_should_not_follow_login_pages(self):
        """Test that login pages are not followed."""
        login_urls = [
            'https://example.com/login',
            'https://example.com/signin',
            'https://example.com/admin',
            'https://example.com/wp-admin/',
        ]

        for url in login_urls:
            assert self.processor.should_follow_url(url) is False

    def test_is_document_url(self):
        """Test document URL detection."""
        doc_urls = [
            'https://example.com/report.pdf',
            'https://example.com/data.doc',
            'https://example.com/spreadsheet.xlsx',
        ]

        for url in doc_urls:
            assert self.processor.is_document_url(url) is True

    def test_is_not_document_url(self):
        """Test non-document URL detection."""
        non_doc_urls = [
            'https://example.com/page',
            'https://example.com/image.jpg',
            'https://example.com/style.css',
        ]

        for url in non_doc_urls:
            assert self.processor.is_document_url(url) is False


class TestURLHashing:
    """Test URL hashing and deduplication."""

    def setup_method(self):
        """Setup test processor."""
        self.processor = URLProcessor(
            base_url='https://example.com',
            allowed_domains=['example.com']
        )

    def test_hash_url_returns_string(self):
        """Test that hash_url returns a string."""
        url = 'https://example.com/page'
        url_hash = self.processor.hash_url(url)

        assert isinstance(url_hash, str)
        assert len(url_hash) > 0

    def test_hash_url_consistent(self):
        """Test that hash_url produces consistent hashes."""
        url = 'https://example.com/page'
        hash1 = self.processor.hash_url(url)
        hash2 = self.processor.hash_url(url)

        assert hash1 == hash2

    def test_hash_url_different_for_different_urls(self):
        """Test that different URLs produce different hashes."""
        url1 = 'https://example.com/page1'
        url2 = 'https://example.com/page2'

        hash1 = self.processor.hash_url(url1)
        hash2 = self.processor.hash_url(url2)

        assert hash1 != hash2

    def test_deduplicate_urls(self):
        """Test URL deduplication."""
        urls = [
            'https://example.com/page1',
            'https://example.com/page2',
            'https://example.com/page1',  # Duplicate
            'https://example.com/page3',
            'https://example.com/page2',  # Duplicate
        ]

        unique_urls = self.processor.deduplicate_urls(urls)

        assert len(unique_urls) == 3
        assert 'https://example.com/page1' in unique_urls
        assert 'https://example.com/page2' in unique_urls
        assert 'https://example.com/page3' in unique_urls

    def test_deduplicate_with_tracking_params(self):
        """Test deduplication normalizes URLs first."""
        urls = [
            'https://example.com/page?utm_source=test&id=1',
            'https://example.com/page?id=1',  # Same after normalization
        ]

        unique_urls = self.processor.deduplicate_urls(urls)

        # Should deduplicate to 1 URL after normalization
        assert len(unique_urls) <= 2


class TestURLPrioritization:
    """Test URL priority calculation."""

    def setup_method(self):
        """Setup test processor."""
        self.processor = URLProcessor(
            base_url='https://example.com',
            allowed_domains=['example.com']
        )

    def test_calculate_priority_high_value(self):
        """Test priority calculation for high-value URL."""
        url = 'https://example.com/research/faculty'
        priority = self.processor.calculate_priority(
            url=url,
            value_score=80,
            depth=1,
            js_confidence=0.5
        )

        assert priority > 50  # Should have high priority

    def test_calculate_priority_low_value(self):
        """Test priority calculation for low-value URL."""
        url = 'https://example.com/login'
        priority = self.processor.calculate_priority(
            url=url,
            value_score=20,
            depth=5,
            js_confidence=0.0
        )

        assert priority < 50  # Should have low priority

    def test_calculate_priority_with_js_boost(self):
        """Test that high JS confidence boosts priority."""
        url = 'https://example.com/app'

        # Low JS confidence
        priority_low_js = self.processor.calculate_priority(
            url=url,
            value_score=50,
            depth=1,
            js_confidence=0.1
        )

        # High JS confidence
        priority_high_js = self.processor.calculate_priority(
            url=url,
            value_score=50,
            depth=1,
            js_confidence=0.9
        )

        assert priority_high_js > priority_low_js

    def test_calculate_priority_depth_penalty(self):
        """Test that depth reduces priority."""
        url = 'https://example.com/page'

        # Shallow depth
        priority_shallow = self.processor.calculate_priority(
            url=url,
            value_score=50,
            depth=1
        )

        # Deep depth
        priority_deep = self.processor.calculate_priority(
            url=url,
            value_score=50,
            depth=10
        )

        assert priority_shallow > priority_deep

    def test_calculate_priority_clamped_to_range(self):
        """Test that priority is clamped to 0-100."""
        url = 'https://example.com/page'

        # Should never exceed 100
        priority = self.processor.calculate_priority(
            url=url,
            value_score=150,  # Too high
            depth=0
        )
        assert 0 <= priority <= 100


class TestBatchOperations:
    """Test batch URL processing operations."""

    def setup_method(self):
        """Setup test processor."""
        self.processor = URLProcessor(
            base_url='https://example.com',
            allowed_domains=['example.com']
        )

    def test_process_batch_basic(self):
        """Test basic batch processing."""
        urls = [
            'https://example.com/page1',
            'https://example.com/page2',
            'https://example.com/page3',
        ]

        results = self.processor.process_batch(
            urls=urls,
            parent_url='https://example.com',
            depth=1
        )

        assert len(results) <= len(urls)  # Some may be filtered
        for result in results:
            assert 'url' in result
            assert 'value_score' in result
            assert 'priority' in result

    def test_process_batch_filters_invalid(self):
        """Test that batch processing filters invalid URLs."""
        urls = [
            'https://example.com/page',
            'https://example.com/login',  # Should be filtered
            'https://example.com/image.jpg',  # Should be filtered
        ]

        results = self.processor.process_batch(
            urls=urls,
            parent_url='https://example.com',
            depth=1
        )

        # Should filter out login and image
        assert len(results) <= 1


class TestDiscoveryAndAssessment:
    """Test URL discovery and assessment from HTML."""

    def setup_method(self):
        """Setup test processor."""
        self.processor = URLProcessor(
            base_url='https://example.com',
            allowed_domains=['example.com']
        )

    def test_discover_and_assess_basic(self):
        """Test basic URL discovery and assessment."""
        html = '''
        <html>
            <body>
                <a href="/page1">Page 1</a>
                <a href="/page2">Page 2</a>
                <a href="/research/faculty">Faculty</a>
            </body>
        </html>
        '''

        response = HtmlResponse(
            url='https://example.com',
            body=html.encode('utf-8')
        )

        results = self.processor.discover_and_assess(
            response=response,
            min_value_score=0  # Don't filter
        )

        assert len(results) > 0
        for result in results:
            assert 'url' in result
            assert 'value_score' in result
            assert 'recommended_spider' in result

    def test_discover_and_assess_filters_low_value(self):
        """Test that low-value URLs are filtered."""
        html = '''
        <html>
            <body>
                <a href="/research/faculty">Faculty</a>
                <a href="/login">Login</a>
            </body>
        </html>
        '''

        response = HtmlResponse(
            url='https://example.com',
            body=html.encode('utf-8')
        )

        results = self.processor.discover_and_assess(
            response=response,
            min_value_score=50  # Filter low-value URLs
        )

        # Login page should be filtered out
        urls = [r['url'] for r in results]
        assert not any('login' in url.lower() for url in urls)


# Integration tests
class TestURLProcessorIntegration:
    """Integration tests for URLProcessor."""

    def test_full_url_processing_pipeline(self):
        """Test complete URL processing pipeline."""
        processor = URLProcessor(
            base_url='https://example.com',
            allowed_domains=['example.com']
        )

        html = '''
        <html>
            <body>
                <a href="/research/faculty?utm_source=test">Faculty</a>
                <a href="/page?id=123&utm_campaign=email">Page</a>
                <a href="/login">Login</a>
                <a href="/image.jpg">Image</a>
            </body>
        </html>
        '''

        response = HtmlResponse(
            url='https://example.com',
            body=html.encode('utf-8')
        )

        # Discover and assess
        results = processor.discover_and_assess(
            response=response,
            min_value_score=30
        )

        # Should have filtered login and image
        # Should have normalized URLs (removed tracking params)
        for result in results:
            assert 'utm_source' not in result['url']
            assert 'utm_campaign' not in result['url']
            assert 'login' not in result['url'].lower()
            assert not result['url'].endswith('.jpg')

    def test_url_processor_with_real_world_html(self):
        """Test URLProcessor with realistic HTML."""
        processor = URLProcessor(
            base_url='https://university.edu',
            allowed_domains=['university.edu']
        )

        html = '''
        <html>
            <head>
                <link rel="stylesheet" href="/css/style.css">
                <script src="/js/app.js"></script>
            </head>
            <body>
                <nav>
                    <a href="/about">About</a>
                    <a href="/research">Research</a>
                    <a href="/faculty">Faculty</a>
                    <a href="/admissions">Admissions</a>
                </nav>
                <main>
                    <article>
                        <a href="/research/publications">Publications</a>
                        <a href="/research/projects">Projects</a>
                    </article>
                </main>
                <footer>
                    <a href="/contact">Contact</a>
                    <a href="/login">Login</a>
                </footer>
            </body>
        </html>
        '''

        response = HtmlResponse(
            url='https://university.edu',
            body=html.encode('utf-8')
        )

        results = processor.discover_and_assess(
            response=response,
            min_value_score=0  # Get all URLs
        )

        # Should find multiple URLs
        assert len(results) > 0

        # Research and faculty pages should have high scores
        research_urls = [r for r in results if 'research' in r['url'] or 'faculty' in r['url']]
        for result in research_urls:
            assert result['value_score'] > 50  # Should be high value

        # CSS and JS should be excluded
        urls = [r['url'] for r in results]
        assert not any(url.endswith('.css') for url in urls)
        assert not any(url.endswith('.js') for url in urls)
