"""
Content classification and enrichment for Stage 2 validation.
Goes beyond "is it HTML?" to provide rich metadata for Stage 3.
"""

import logging
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class ContentCategory(Enum):
    """Content category classification"""
    HTML_PAGE = "html_page"  # Standard HTML page
    API_ENDPOINT = "api_endpoint"  # JSON/XML API
    DOCUMENT = "document"  # PDF, DOC, etc.
    IMAGE = "image"  # Image files
    VIDEO = "video"  # Video files
    AUDIO = "audio"  # Audio files
    ARCHIVE = "archive"  # ZIP, TAR, etc.
    CODE = "code"  # Source code files
    DATA = "data"  # CSV, JSON data files
    REDIRECT = "redirect"  # Redirect responses
    ERROR = "error"  # Error pages
    UNKNOWN = "unknown"  # Cannot classify


class ContentQuality(Enum):
    """Content quality assessment"""
    HIGH = "high"  # Rich content, worth enriching
    MEDIUM = "medium"  # Standard content
    LOW = "low"  # Thin content, may skip enrichment
    ERROR = "error"  # Error content


@dataclass
class ClassificationResult:
    """Result of content classification"""
    category: ContentCategory
    quality: ContentQuality
    mime_type: str
    mime_type_family: str  # text, image, video, audio, application
    is_enrichable: bool  # Worth sending to Stage 3
    confidence: float  # 0.0-1.0 confidence in classification
    metadata: Dict[str, Any]  # Additional metadata
    recommendations: List[str]  # Processing recommendations

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSONL output"""
        return {
            'category': self.category.value,
            'quality': self.quality.value,
            'mime_type': self.mime_type,
            'mime_type_family': self.mime_type_family,
            'is_enrichable': self.is_enrichable,
            'confidence': self.confidence,
            'metadata': self.metadata,
            'recommendations': self.recommendations
        }


class ContentClassifier:
    """
    Classifies content beyond simple HTML check.
    Provides rich metadata for Stage 3 decision making.
    """

    def __init__(self):
        """Initialize classifier with content type mappings"""
        self.mime_to_category = {
            # HTML/Web
            'text/html': ContentCategory.HTML_PAGE,
            'application/xhtml+xml': ContentCategory.HTML_PAGE,

            # APIs
            'application/json': ContentCategory.API_ENDPOINT,
            'application/xml': ContentCategory.API_ENDPOINT,
            'text/xml': ContentCategory.API_ENDPOINT,
            'application/ld+json': ContentCategory.API_ENDPOINT,

            # Documents
            'application/pdf': ContentCategory.DOCUMENT,
            'application/msword': ContentCategory.DOCUMENT,
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ContentCategory.DOCUMENT,
            'application/vnd.ms-excel': ContentCategory.DOCUMENT,
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ContentCategory.DOCUMENT,

            # Images
            'image/jpeg': ContentCategory.IMAGE,
            'image/png': ContentCategory.IMAGE,
            'image/gif': ContentCategory.IMAGE,
            'image/webp': ContentCategory.IMAGE,
            'image/svg+xml': ContentCategory.IMAGE,

            # Video
            'video/mp4': ContentCategory.VIDEO,
            'video/webm': ContentCategory.VIDEO,
            'video/ogg': ContentCategory.VIDEO,
            'video/avi': ContentCategory.VIDEO,

            # Audio
            'audio/mpeg': ContentCategory.AUDIO,
            'audio/mp3': ContentCategory.AUDIO,
            'audio/wav': ContentCategory.AUDIO,
            'audio/ogg': ContentCategory.AUDIO,

            # Archives
            'application/zip': ContentCategory.ARCHIVE,
            'application/x-gzip': ContentCategory.ARCHIVE,
            'application/x-tar': ContentCategory.ARCHIVE,

            # Code
            'text/javascript': ContentCategory.CODE,
            'application/javascript': ContentCategory.CODE,
            'text/css': ContentCategory.CODE,
            'text/x-python': ContentCategory.CODE,

            # Data
            'text/csv': ContentCategory.DATA,
            'application/vnd.ms-excel': ContentCategory.DATA,
        }

    def classify(
        self,
        status_code: int,
        content_type: str,
        content_length: int,
        url: str,
        headers: Optional[Dict[str, str]] = None
    ) -> ClassificationResult:
        """
        Classify content and assess quality

        Args:
            status_code: HTTP status code
            content_type: Content-Type header
            content_length: Content length in bytes
            url: URL being classified
            headers: Optional HTTP headers

        Returns:
            ClassificationResult with category, quality, and metadata
        """
        # Normalize content type
        normalized_type = content_type.split(';')[0].strip().lower()
        mime_family = normalized_type.split('/')[0] if '/' in normalized_type else 'unknown'

        # Handle redirects
        if 300 <= status_code < 400:
            return self._classify_redirect(status_code, headers, url)

        # Handle errors
        if status_code >= 400:
            return self._classify_error(status_code, content_length, url)

        # Classify by content type
        category = self.mime_to_category.get(normalized_type, ContentCategory.UNKNOWN)

        # Assess quality
        quality = self._assess_quality(
            category, status_code, content_length, normalized_type, url
        )

        # Determine if enrichable
        is_enrichable = self._is_enrichable(category, quality, content_length)

        # Calculate confidence
        confidence = self._calculate_confidence(
            category, normalized_type, content_length
        )

        # Generate metadata
        metadata = self._generate_metadata(
            category, status_code, content_length, url, headers
        )

        # Generate recommendations
        recommendations = self._generate_recommendations(
            category, quality, content_length, url
        )

        return ClassificationResult(
            category=category,
            quality=quality,
            mime_type=normalized_type,
            mime_type_family=mime_family,
            is_enrichable=is_enrichable,
            confidence=confidence,
            metadata=metadata,
            recommendations=recommendations
        )

    def _classify_redirect(
        self,
        status_code: int,
        headers: Optional[Dict[str, str]],
        url: str
    ) -> ClassificationResult:
        """Classify redirect responses"""
        redirect_location = headers.get('Location', '') if headers else ''

        return ClassificationResult(
            category=ContentCategory.REDIRECT,
            quality=ContentQuality.MEDIUM,
            mime_type='',
            mime_type_family='redirect',
            is_enrichable=False,
            confidence=1.0,
            metadata={
                'redirect_type': 'permanent' if status_code in (301, 308) else 'temporary',
                'location': redirect_location
            },
            recommendations=['follow_redirect']
        )

    def _classify_error(
        self,
        status_code: int,
        content_length: int,
        url: str
    ) -> ClassificationResult:
        """Classify error responses"""
        return ClassificationResult(
            category=ContentCategory.ERROR,
            quality=ContentQuality.ERROR,
            mime_type='',
            mime_type_family='error',
            is_enrichable=False,
            confidence=1.0,
            metadata={
                'error_class': 'client_error' if 400 <= status_code < 500 else 'server_error',
                'is_permanent': status_code in (404, 410)
            },
            recommendations=['skip_enrichment']
        )

    def _assess_quality(
        self,
        category: ContentCategory,
        status_code: int,
        content_length: int,
        mime_type: str,
        url: str
    ) -> ContentQuality:
        """Assess content quality"""
        # Error content
        if status_code >= 400:
            return ContentQuality.ERROR

        # HTML quality assessment
        if category == ContentCategory.HTML_PAGE:
            if content_length < 500:
                return ContentQuality.LOW  # Too small, likely stub
            elif content_length > 10000:
                return ContentQuality.HIGH  # Rich content
            else:
                return ContentQuality.MEDIUM

        # Documents are typically high quality
        if category == ContentCategory.DOCUMENT:
            if content_length > 1000:
                return ContentQuality.HIGH
            else:
                return ContentQuality.LOW

        # APIs are medium quality
        if category == ContentCategory.API_ENDPOINT:
            return ContentQuality.MEDIUM

        # Media files are medium quality
        if category in (ContentCategory.IMAGE, ContentCategory.VIDEO, ContentCategory.AUDIO):
            return ContentQuality.MEDIUM

        # Default
        return ContentQuality.MEDIUM

    def _is_enrichable(
        self,
        category: ContentCategory,
        quality: ContentQuality,
        content_length: int
    ) -> bool:
        """Determine if content is worth enriching in Stage 3"""
        # Skip error content
        if quality == ContentQuality.ERROR:
            return False

        # Skip very low quality
        if quality == ContentQuality.LOW and content_length < 100:
            return False

        # Enrich HTML pages
        if category == ContentCategory.HTML_PAGE:
            return True

        # Enrich documents
        if category == ContentCategory.DOCUMENT:
            return True

        # Enrich some APIs (may contain text content)
        if category == ContentCategory.API_ENDPOINT:
            return True

        # Skip media, archives, code
        if category in (
            ContentCategory.IMAGE,
            ContentCategory.VIDEO,
            ContentCategory.AUDIO,
            ContentCategory.ARCHIVE,
            ContentCategory.CODE
        ):
            return False

        # Default: enrich if medium+ quality
        return quality in (ContentQuality.MEDIUM, ContentQuality.HIGH)

    def _calculate_confidence(
        self,
        category: ContentCategory,
        mime_type: str,
        content_length: int
    ) -> float:
        """Calculate confidence in classification"""
        # High confidence for known mime types
        if mime_type in self.mime_to_category:
            return 0.95

        # Lower confidence for unknown
        if category == ContentCategory.UNKNOWN:
            return 0.3

        # Medium confidence otherwise
        return 0.7

    def _generate_metadata(
        self,
        category: ContentCategory,
        status_code: int,
        content_length: int,
        url: str,
        headers: Optional[Dict[str, str]]
    ) -> Dict[str, Any]:
        """Generate additional metadata"""
        metadata = {
            'has_query_params': '?' in url,
            'url_depth': url.count('/') - 2,  # Subtract protocol slashes
            'content_size_class': self._classify_size(content_length)
        }

        # Add header hints if available
        if headers:
            if 'Last-Modified' in headers:
                metadata['last_modified'] = headers['Last-Modified']
            if 'ETag' in headers:
                metadata['has_etag'] = True
            if 'Cache-Control' in headers:
                metadata['cacheable'] = 'no-cache' not in headers['Cache-Control'].lower()

        return metadata

    def _classify_size(self, content_length: int) -> str:
        """Classify content size"""
        if content_length < 1024:
            return 'tiny'  # < 1KB
        elif content_length < 10 * 1024:
            return 'small'  # < 10KB
        elif content_length < 100 * 1024:
            return 'medium'  # < 100KB
        elif content_length < 1024 * 1024:
            return 'large'  # < 1MB
        else:
            return 'very_large'  # >= 1MB

    def _generate_recommendations(
        self,
        category: ContentCategory,
        quality: ContentQuality,
        content_length: int,
        url: str
    ) -> List[str]:
        """Generate processing recommendations"""
        recommendations = []

        # Enrichment recommendations
        if category == ContentCategory.HTML_PAGE:
            recommendations.append('extract_text')
            recommendations.append('extract_links')
            if quality == ContentQuality.HIGH:
                recommendations.append('extract_entities')
                recommendations.append('extract_keywords')

        if category == ContentCategory.DOCUMENT:
            recommendations.append('extract_text')
            recommendations.append('extract_metadata')

        if category == ContentCategory.API_ENDPOINT:
            recommendations.append('parse_json')
            recommendations.append('extract_data_fields')

        # Quality-based recommendations
        if quality == ContentQuality.LOW:
            recommendations.append('low_priority')
        elif quality == ContentQuality.HIGH:
            recommendations.append('high_priority')

        # Size-based recommendations
        if content_length > 1024 * 1024:  # > 1MB
            recommendations.append('large_content')
            recommendations.append('stream_processing')

        return recommendations


def classify_content(
    status_code: int,
    content_type: str,
    content_length: int,
    url: str,
    headers: Optional[Dict[str, str]] = None
) -> ClassificationResult:
    """
    Convenience function to classify content

    Args:
        status_code: HTTP status code
        content_type: Content-Type header
        content_length: Content length in bytes
        url: URL being classified
        headers: Optional HTTP headers

    Returns:
        ClassificationResult
    """
    classifier = ContentClassifier()
    return classifier.classify(status_code, content_type, content_length, url, headers)
