"""Unit tests for IntelligentAnalyzer quality control and triage logic."""

import pytest

from src.stage2.intelligent_analyzer import IntelligentAnalyzer


class TestIntelligentAnalyzer:
    """Test IntelligentAnalyzer class."""

    @pytest.fixture
    def analyzer(self):
        """Create analyzer instance."""
        analyzer = IntelligentAnalyzer()
        yield analyzer
        analyzer.close()

    def test_quality_score_calculation(self, analyzer):
        """Test quality score calculation."""
        # Low quality (few words, low ratio)
        score = analyzer._calculate_quality_score(50, 0.1)
        assert 0 <= score <= 1
        assert score < 0.1

        # Medium quality
        score = analyzer._calculate_quality_score(500, 0.3)
        assert 0.3 <= score <= 0.5

        # High quality (many words, good ratio)
        score = analyzer._calculate_quality_score(1000, 0.5)
        assert score >= 0.6

    def test_html_analysis_quality_detection(self, analyzer):
        """Test HTML analysis correctly identifies low quality content."""
        # Low quality HTML (sparse content)
        low_quality_html = """
        <html>
            <body>
                <nav>Navigation stuff</nav>
                <p>Just a few words here.</p>
                <script>console.log('lots of javascript');</script>
            </body>
        </html>
        """

        result = analyzer._analyze_html('http://example.com', low_quality_html, is_heavy=False)

        assert result['is_low_quality'] is True
        assert result['word_count'] < analyzer.MIN_WORD_COUNT
        assert result['quality_score'] < 0.3

    def test_html_analysis_removes_noise(self, analyzer):
        """Test that noise elements are removed from HTML."""
        html_with_noise = """
        <html>
            <body>
                <nav>Navigation menu</nav>
                <header>Header content</header>
                <script>var x = 1;</script>
                <p>This is actual content that should be extracted and counted properly.</p>
                <p>More real content here with meaningful information.</p>
                <p>Additional paragraph with useful text.</p>
                <footer>Footer info</footer>
            </body>
        </html>
        """

        result = analyzer._analyze_html('http://example.com', html_with_noise, is_heavy=False)

        # Noise should be removed
        assert 'Navigation menu' not in result['text_extracted']
        assert 'Header content' not in result['text_extracted']
        assert 'var x = 1' not in result['text_extracted']

        # Real content should be present
        assert 'actual content' in result['text_extracted']

    def test_massive_document_detection(self, analyzer):
        """Test massive document detection and routing."""
        # Create large HTML content
        large_text = "This is a sentence with ten words in it. " * 10000  # ~100k words
        large_html = f"<html><body><p>{large_text}</p></body></html>"

        result = analyzer._analyze_html('http://example.com/large', large_html, is_heavy=False)

        assert result['is_massive_doc'] is True
        assert result['content_length'] > analyzer.MASSIVE_DOC_THRESHOLD

    def test_normal_document_processing(self, analyzer):
        """Test normal-sized quality document processing."""
        normal_html = """
        <html>
            <body>
                <h1>Quality Article</h1>
                <p>This is a well-written article with substantial content that provides value.</p>
                <p>It has multiple paragraphs covering various aspects of the topic.</p>
                <p>The content is rich and informative with good text-to-HTML ratio.</p>
                <p>This ensures it passes quality thresholds and gets processed normally.</p>
                <p>Additional meaningful content to meet word count requirements.</p>
            </body>
        </html>
        """

        result = analyzer._analyze_html('http://example.com/quality', normal_html, is_heavy=False)

        assert result['is_low_quality'] is False
        assert result['is_massive_doc'] is False
        assert result['word_count'] >= analyzer.MIN_WORD_COUNT
        assert result['quality_score'] > 0

    def test_error_record_creation(self, analyzer):
        """Test error record structure."""
        error_record = analyzer._error_record('http://example.com', 404, 'Not Found')

        assert error_record['has_error'] is True
        assert error_record['is_404'] is True
        assert error_record['error_code'] == 404
        assert error_record['error_message'] == 'Not Found'
        assert error_record['is_low_quality'] is True
        assert error_record['quality_score'] == 0

    def test_text_to_html_ratio(self, analyzer):
        """Test text-to-HTML ratio calculation."""
        # High ratio (good content)
        good_html = "<html><body><p>" + "word " * 100 + "</p></body></html>"
        result = analyzer._analyze_html('http://example.com', good_html, is_heavy=False)
        assert result['text_to_html_ratio'] > analyzer.MIN_TEXT_TO_HTML_RATIO

        # Low ratio (bloated HTML)
        bloated_html = "<html><body>" + "<div><span>" * 50 + "word" + "</span></div>" * 50 + "</body></html>"
        result = analyzer._analyze_html('http://example.com', bloated_html, is_heavy=False)
        # Should be low quality due to poor ratio
        assert result['text_to_html_ratio'] < 0.05

    def test_pdf_link_detection(self, analyzer):
        """Test PDF link detection in HTML."""
        html_with_pdfs = """
        <html>
            <body>
                <p>Check out these documents:</p>
                <a href="/document1.pdf">Document 1</a>
                <a href="https://example.com/file.pdf">Document 2</a>
                <a href="/page.html">Regular link</a>
            </body>
        </html>
        """

        result = analyzer._analyze_html('http://example.com', html_with_pdfs, is_heavy=False)

        assert result['has_pdf'] is True
        assert len(result['pdf_links']) == 2
        assert any('.pdf' in link for link in result['pdf_links'])
