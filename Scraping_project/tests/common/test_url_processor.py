"""Tests for centralized URL processor filtering logic."""

import pytest

from src.common.url_processor import should_follow_url


class TestShouldFollowUrl:
    """Test the centralized URL filtering logic (liberal Stage 1 policy)."""

    # ============================================================================
    # Positive Cases - URLs that SHOULD be followed
    # ============================================================================

    @pytest.mark.parametrize(
        "url,description",
        [
            # HTML pages
            ("https://example.com/page.html", "HTML page"),
            ("https://example.com/index.htm", "HTM page"),
            ("https://example.com/", "Root path"),
            ("https://example.com/about", "No extension (likely HTML)"),
            ("https://example.com/faculty/directory", "Directory path"),
            # Documents (valuable)
            ("https://example.com/doc.pdf", "PDF document"),
            ("https://example.com/report.docx", "Word document"),
            ("https://example.com/data.xlsx", "Excel spreadsheet"),
            ("https://example.com/presentation.pptx", "PowerPoint presentation"),
            ("https://example.com/file.doc", "Legacy Word document"),
            ("https://example.com/sheet.xls", "Legacy Excel"),
            ("https://example.com/slides.ppt", "Legacy PowerPoint"),
            # JavaScript (may contain dynamic content/links)
            ("https://example.com/app.js", "JavaScript file"),
            ("https://example.com/bundle.min.js", "Minified JS bundle"),
            # SVG (can contain links)
            ("https://example.com/diagram.svg", "SVG image"),
            # Archives (may have directory listings)
            ("https://example.com/archive.zip", "ZIP archive"),
            ("https://example.com/backup.tar.gz", "Tarball"),
            ("https://example.com/data.rar", "RAR archive"),
            ("https://example.com/files.7z", "7-Zip archive"),
            # Sitemaps and feeds
            ("https://example.com/sitemap.xml", "Sitemap"),
            ("https://example.com/feed.rss", "RSS feed"),
            ("https://example.com/atom.xml", "Atom feed"),
            # API endpoints (may have documentation)
            ("https://example.com/api/users", "API endpoint"),
            ("https://example.com/api/v1/docs", "API docs"),
            # Admin pages (may have public directory info)
            ("https://example.com/admin/directory", "Admin directory"),
            ("https://example.com/admin/faculty", "Admin faculty list"),
            # Login pages (may have SAML/SSO links)
            ("https://example.com/login/saml", "SAML login"),
            ("https://example.com/login/shibboleth", "Shibboleth login"),
            ("https://example.com/login", "Generic login"),
            # Other valuable content
            ("https://example.com/research/publications", "Research page"),
            ("https://example.com/courses/2024/spring", "Course page"),
            ("https://example.com/news/2024/01/article", "News article"),
        ],
    )
    def test_should_follow_valid_urls(self, url: str, description: str):
        """Test that valuable URLs are allowed (liberal policy)."""
        assert should_follow_url(url), f"Should follow {description}: {url}"

    # ============================================================================
    # Negative Cases - URLs that should NOT be followed
    # ============================================================================

    @pytest.mark.parametrize(
        "url,description",
        [
            # Pure binary images
            ("https://example.com/photo.jpg", "JPEG image"),
            ("https://example.com/image.jpeg", "JPEG image variant"),
            ("https://example.com/graphic.png", "PNG image"),
            ("https://example.com/animation.gif", "GIF image"),
            ("https://example.com/bitmap.bmp", "Bitmap image"),
            ("https://example.com/photo.webp", "WebP image"),
            ("https://example.com/favicon.ico", "Icon file"),
            ("https://example.com/image.tiff", "TIFF image"),
            # Stylesheets and source maps
            ("https://example.com/styles.css", "CSS stylesheet"),
            ("https://example.com/app.css.map", "CSS source map"),
            ("https://example.com/bundle.js.map", "JS source map"),
            # Media files
            ("https://example.com/song.mp3", "MP3 audio"),
            ("https://example.com/video.mp4", "MP4 video"),
            ("https://example.com/clip.avi", "AVI video"),
            ("https://example.com/movie.mov", "MOV video"),
            ("https://example.com/media.wmv", "WMV video"),
            ("https://example.com/stream.flv", "FLV video"),
            ("https://example.com/video.webm", "WebM video"),
            ("https://example.com/audio.m4a", "M4A audio"),
            ("https://example.com/sound.wav", "WAV audio"),
            # Fonts
            ("https://example.com/font.woff", "WOFF font"),
            ("https://example.com/font.woff2", "WOFF2 font"),
            ("https://example.com/font.ttf", "TrueType font"),
            ("https://example.com/font.eot", "EOT font"),
            ("https://example.com/font.otf", "OpenType font"),
            # Binary executables
            ("https://example.com/installer.exe", "Windows executable"),
            ("https://example.com/app.dmg", "macOS disk image"),
            ("https://example.com/package.pkg", "macOS package"),
            ("https://example.com/package.deb", "Debian package"),
            ("https://example.com/package.rpm", "RPM package"),
            # Problematic endpoints (exclusion patterns)
            ("https://example.com/wp-login.php", "WordPress login"),
            ("https://example.com/checkout", "E-commerce checkout"),
        ],
    )
    def test_should_not_follow_static_assets(self, url: str, description: str):
        """Test that static/binary assets are blocked."""
        assert not should_follow_url(url), f"Should NOT follow {description}: {url}"

    # ============================================================================
    # Edge Cases
    # ============================================================================

    def test_case_insensitive_extension_matching(self):
        """Test that extension matching is case-insensitive."""
        assert not should_follow_url("https://example.com/IMAGE.JPG")
        assert not should_follow_url("https://example.com/Photo.PNG")
        assert not should_follow_url("https://example.com/STYLES.CSS")

    def test_query_parameters_ignored(self):
        """Test that query parameters don't affect filtering."""
        # Should follow
        assert should_follow_url("https://example.com/page.html?id=123")
        assert should_follow_url("https://example.com/doc.pdf?download=true")

        # Should not follow
        assert not should_follow_url("https://example.com/image.jpg?size=large")
        assert not should_follow_url("https://example.com/video.mp4?quality=hd")

    def test_fragment_identifiers_ignored(self):
        """Test that fragment identifiers don't affect filtering."""
        # Should follow
        assert should_follow_url("https://example.com/page.html#section")
        assert should_follow_url("https://example.com/doc.pdf#page=5")

        # Should not follow
        assert not should_follow_url("https://example.com/image.jpg#zoom")

    def test_extension_in_path_but_not_at_end(self):
        """Test that extensions in the middle of the path don't trigger filtering."""
        # These should all be followed (extension is not at the end)
        assert should_follow_url("https://example.com/image.jpg/metadata")
        assert should_follow_url("https://example.com/files.css/documentation")
        assert should_follow_url("https://example.com/photo.png/gallery")

    def test_invalid_urls(self):
        """Test that invalid URLs are handled gracefully."""
        assert not should_follow_url("")
        assert not should_follow_url("not-a-url")
        assert not should_follow_url("ftp://example.com/file.txt")  # Non-HTTP(S)

    def test_urls_without_extension(self):
        """Test that URLs without file extensions are followed (likely HTML)."""
        assert should_follow_url("https://example.com/about")
        assert should_follow_url("https://example.com/faculty/directory")
        assert should_follow_url("https://example.com/research")

    def test_wordpress_login_blocked(self):
        """Test that WordPress login is specifically blocked."""
        assert not should_follow_url("https://example.com/wp-login.php")
        assert not should_follow_url("https://example.com/blog/wp-login.php")

    def test_checkout_endpoint_blocked(self):
        """Test that checkout endpoint is specifically blocked."""
        assert not should_follow_url("https://example.com/checkout")
        assert not should_follow_url("https://example.com/store/checkout")

    def test_liberal_policy_examples(self):
        """Test that liberal policy allows controversial but potentially valuable URLs."""
        # These should all be followed (liberal Stage 1 policy)
        assert should_follow_url("https://example.com/app.js")  # May contain dynamic content
        assert should_follow_url("https://example.com/diagram.svg")  # May contain links
        assert should_follow_url("https://example.com/archive.zip")  # May have directory listing
        assert should_follow_url("https://example.com/admin/faculty")  # May have public info
        assert should_follow_url("https://example.com/login/saml")  # May have SSO links


class TestURLProcessorIntegration:
    """Integration tests for URL filtering with realistic scenarios."""

    def test_university_website_urls(self):
        """Test realistic university website URL filtering."""
        # Should follow (valuable university content)
        university_urls_to_follow = [
            "https://uconn.edu/faculty/directory",
            "https://uconn.edu/research/publications",
            "https://uconn.edu/courses/catalog",
            "https://uconn.edu/news/2024/announcement",
            "https://uconn.edu/about/history",
            "https://uconn.edu/admissions",
            "https://uconn.edu/departments/biology",
            "https://uconn.edu/syllabus.pdf",
            "https://uconn.edu/lecture-notes.docx",
        ]

        for url in university_urls_to_follow:
            assert should_follow_url(url), f"Should follow university URL: {url}"

        # Should not follow (static assets)
        university_urls_to_skip = [
            "https://uconn.edu/images/logo.png",
            "https://uconn.edu/assets/banner.jpg",
            "https://uconn.edu/css/main.css",
            "https://uconn.edu/videos/promo.mp4",
            "https://uconn.edu/fonts/roboto.woff2",
        ]

        for url in university_urls_to_skip:
            assert not should_follow_url(url), f"Should skip static asset: {url}"

    def test_consistency_across_spiders(self):
        """Test that filtering is consistent across different spider use cases."""
        test_url = "https://example.com/photo.jpg"

        # Should consistently return False for static assets
        assert not should_follow_url(test_url)
        assert not should_follow_url(test_url)  # Call again to ensure consistency

        test_url2 = "https://example.com/document.pdf"

        # Should consistently return True for documents
        assert should_follow_url(test_url2)
        assert should_follow_url(test_url2)  # Call again to ensure consistency
