"""Tests for centralized URL processor filtering logic."""

import pytest

from src.common.url_processor import should_follow_url

class TestShouldFollowUrl:

    # ============================================================================
    # ============================================================================

    @pytest.mark.parametrize(
        "url,description",
        [
            ("https://example.com/page.html", "HTML page"),
            ("https://example.com/index.htm", "HTM page"),
            ("https://example.com/", "Root path"),
            ("https://example.com/about", "No extension (likely HTML)"),
            ("https://example.com/faculty/directory", "Directory path"),
            ("https://example.com/doc.pdf", "PDF document"),
            ("https://example.com/report.docx", "Word document"),
            ("https://example.com/data.xlsx", "Excel spreadsheet"),
            ("https://example.com/presentation.pptx", "PowerPoint presentation"),
            ("https://example.com/file.doc", "Legacy Word document"),
            ("https://example.com/sheet.xls", "Legacy Excel"),
            ("https://example.com/slides.ppt", "Legacy PowerPoint"),
            ("https://example.com/app.js", "JavaScript file"),
            ("https://example.com/bundle.min.js", "Minified JS bundle"),
            ("https://example.com/diagram.svg", "SVG image"),
            ("https://example.com/archive.zip", "ZIP archive"),
            ("https://example.com/backup.tar.gz", "Tarball"),
            ("https://example.com/data.rar", "RAR archive"),
            ("https://example.com/files.7z", "7-Zip archive"),
            ("https://example.com/sitemap.xml", "Sitemap"),
            ("https://example.com/feed.rss", "RSS feed"),
            ("https://example.com/atom.xml", "Atom feed"),
            ("https://example.com/api/users", "API endpoint"),
            ("https://example.com/api/v1/docs", "API docs"),
            ("https://example.com/admin/directory", "Admin directory"),
            ("https://example.com/admin/faculty", "Admin faculty list"),
            ("https://example.com/login/saml", "SAML login"),
            ("https://example.com/login/shibboleth", "Shibboleth login"),
            ("https://example.com/login", "Generic login"),
            ("https://example.com/research/publications", "Research page"),
            ("https://example.com/courses/2024/spring", "Course page"),
            ("https://example.com/news/2024/01/article", "News article"),
        ],
    )
    def test_should_follow_valid_urls(self, url: str, description: str):
        assert should_follow_url(url), f"Should follow {description}: {url}"

    # ============================================================================
    # ============================================================================

    @pytest.mark.parametrize(
        "url,description",
        [
            ("https://example.com/photo.jpg", "JPEG image"),
            ("https://example.com/image.jpeg", "JPEG image variant"),
            ("https://example.com/graphic.png", "PNG image"),
            ("https://example.com/animation.gif", "GIF image"),
            ("https://example.com/bitmap.bmp", "Bitmap image"),
            ("https://example.com/photo.webp", "WebP image"),
            ("https://example.com/favicon.ico", "Icon file"),
            ("https://example.com/image.tiff", "TIFF image"),
            ("https://example.com/styles.css", "CSS stylesheet"),
            ("https://example.com/app.css.map", "CSS source map"),
            ("https://example.com/bundle.js.map", "JS source map"),
            ("https://example.com/song.mp3", "MP3 audio"),
            ("https://example.com/video.mp4", "MP4 video"),
            ("https://example.com/clip.avi", "AVI video"),
            ("https://example.com/movie.mov", "MOV video"),
            ("https://example.com/media.wmv", "WMV video"),
            ("https://example.com/stream.flv", "FLV video"),
            ("https://example.com/video.webm", "WebM video"),
            ("https://example.com/audio.m4a", "M4A audio"),
            ("https://example.com/sound.wav", "WAV audio"),
            ("https://example.com/font.woff", "WOFF font"),
            ("https://example.com/font.woff2", "WOFF2 font"),
            ("https://example.com/font.ttf", "TrueType font"),
            ("https://example.com/font.eot", "EOT font"),
            ("https://example.com/font.otf", "OpenType font"),
            ("https://example.com/installer.exe", "Windows executable"),
            ("https://example.com/app.dmg", "macOS disk image"),
            ("https://example.com/package.pkg", "macOS package"),
            ("https://example.com/package.deb", "Debian package"),
            ("https://example.com/package.rpm", "RPM package"),
            ("https://example.com/wp-login.php", "WordPress login"),
            ("https://example.com/checkout", "E-commerce checkout"),
        ],
    )
    def test_should_not_follow_static_assets(self, url: str, description: str):
        assert not should_follow_url(url), f"Should NOT follow {description}: {url}"

    # ============================================================================
    # ============================================================================

    def test_case_insensitive_extension_matching(self):
        assert not should_follow_url("https://example.com/IMAGE.JPG")
        assert not should_follow_url("https://example.com/Photo.PNG")
        assert not should_follow_url("https://example.com/STYLES.CSS")

    def test_query_parameters_ignored(self):
        assert should_follow_url("https://example.com/page.html?id=123")
        assert should_follow_url("https://example.com/doc.pdf?download=true")

        assert not should_follow_url("https://example.com/image.jpg?size=large")
        assert not should_follow_url("https://example.com/video.mp4?quality=hd")

    def test_fragment_identifiers_ignored(self):
        assert should_follow_url("https://example.com/page.html
        assert should_follow_url("https://example.com/doc.pdf

        assert not should_follow_url("https://example.com/image.jpg

    def test_extension_in_path_but_not_at_end(self):
        assert should_follow_url("https://example.com/image.jpg/metadata")
        assert should_follow_url("https://example.com/files.css/documentation")
        assert should_follow_url("https://example.com/photo.png/gallery")

    def test_invalid_urls(self):
        assert not should_follow_url("")
        assert not should_follow_url("not-a-url")
        assert not should_follow_url("ftp://example.com/file.txt")

    def test_urls_without_extension(self):
        assert should_follow_url("https://example.com/about")
        assert should_follow_url("https://example.com/faculty/directory")
        assert should_follow_url("https://example.com/research")

    def test_wordpress_login_blocked(self):
        assert not should_follow_url("https://example.com/wp-login.php")
        assert not should_follow_url("https://example.com/blog/wp-login.php")

    def test_checkout_endpoint_blocked(self):
        assert not should_follow_url("https://example.com/checkout")
        assert not should_follow_url("https://example.com/store/checkout")

    def test_liberal_policy_examples(self):
        assert should_follow_url("https://example.com/app.js")
        assert should_follow_url("https://example.com/diagram.svg")
        assert should_follow_url("https://example.com/archive.zip")
        assert should_follow_url("https://example.com/admin/faculty")
        assert should_follow_url("https://example.com/login/saml")

class TestURLProcessorIntegration:

    def test_university_website_urls(self):
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
        test_url = "https://example.com/photo.jpg"

        assert not should_follow_url(test_url)
        assert not should_follow_url(test_url)

        test_url2 = "https://example.com/document.pdf"

        assert should_follow_url(test_url2)
        assert should_follow_url(test_url2)
