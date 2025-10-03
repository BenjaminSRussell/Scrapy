"""
Advanced Content Analysis Module

Provides content quality scoring, recency detection, academic classification,
language detection, and information density analysis for Stage 3 enrichment.
"""

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


# Academic content indicators
COURSE_INDICATORS = [
    r'\bcourse[s]?\b', r'\bsyllabus\b', r'\blecture[s]?\b', r'\bsemester\b',
    r'\bcredit[s]?\b', r'\bprerequisite[s]?\b', r'\bcurriculum\b',
    r'\b[A-Z]{2,4}\s*\d{3,4}\b',  # Course codes like CS1234, MATH 101
    r'\bspring\s+\d{4}\b', r'\bfall\s+\d{4}\b', r'\bsummer\s+\d{4}\b'
]

RESEARCH_INDICATORS = [
    r'\bresearch\b', r'\bpublication[s]?\b', r'\bjournal\b', r'\bconference\b',
    r'\bcitation[s]?\b', r'\babstract\b', r'\bmanuscript\b', r'\bpaper[s]?\b',
    r'\bgrant[s]?\b', r'\bfunding\b', r'\blaboratory\b', r'\blab\b',
    r'\bdoi\s*:', r'\bissn\s*:', r'\bisbn\s*:'
]

POLICY_INDICATORS = [
    r'\bpolicy\b', r'\bpolicies\b', r'\bregulation[s]?\b', r'\bguideline[s]?\b',
    r'\bprocedure[s]?\b', r'\brequirement[s]?\b', r'\bstandard[s]?\b',
    r'\bcompliance\b', r'\bcode of conduct\b', r'\bhonor code\b'
]

FACULTY_INDICATORS = [
    r'\bfaculty\b', r'\bprofessor\b', r'\binstructor\b', r'\bdepartment\b',
    r'\boffice hours\b', r'\bcurriculum vitae\b', r'\bc\.?v\.?\b',
    r'\bph\.?d\.?\b', r'\bdr\.?\s+\w+\b'
]

# Date patterns for recency detection
DATE_PATTERNS = [
    # ISO format: 2024-01-15
    (r'\b(\d{4})-(\d{2})-(\d{2})\b', '%Y-%m-%d'),
    # US format: January 15, 2024 or Jan 15, 2024
    (r'\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})\b', '%B %d %Y'),
    (r'\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+(\d{1,2}),?\s+(\d{4})\b', '%b %d %Y'),
    # MM/DD/YYYY or MM-DD-YYYY
    (r'\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b', '%m/%d/%Y'),
    # Relative dates
    (r'\b(today|yesterday)\b', 'relative'),
    (r'\b(\d+)\s+(day|week|month|year)s?\s+ago\b', 'relative'),
    # Academic semesters
    (r'\b(Spring|Fall|Summer|Winter)\s+(\d{4})\b', 'semester'),
]

# Updated/Modified indicators
UPDATED_INDICATORS = [
    r'updated\s*:?\s*',
    r'last\s+updated\s*:?\s*',
    r'modified\s*:?\s*',
    r'last\s+modified\s*:?\s*',
    r'revised\s*:?\s*',
    r'last\s+revised\s*:?\s*',
    r'published\s*:?\s*',
]


@dataclass
class ContentQualityScore:
    """Content quality metrics"""
    overall_score: float = 0.0  # 0-100
    information_density: float = 0.0  # 0-1
    readability_score: float = 0.0  # 0-100
    structure_score: float = 0.0  # 0-100
    freshness_score: float = 0.0  # 0-100

    # Detailed metrics
    word_count: int = 0
    sentence_count: int = 0
    paragraph_count: int = 0
    avg_sentence_length: float = 0.0
    avg_paragraph_length: float = 0.0
    unique_word_ratio: float = 0.0

    # Content indicators
    has_headings: bool = False
    has_lists: bool = False
    has_tables: bool = False
    has_links: int = 0
    has_images: int = 0

    # Quality flags
    is_substantive: bool = False  # More than minimal content
    is_navigation_page: bool = False  # Mostly links
    is_event_page: bool = False


@dataclass
class RecencyInfo:
    """Recency and timestamp information"""
    most_recent_date: datetime | None = None
    oldest_date: datetime | None = None
    all_dates: list[datetime] = field(default_factory=list)
    date_count: int = 0

    has_recent_content: bool = False  # Within last year
    has_very_recent_content: bool = False  # Within last 30 days
    days_since_update: int | None = None

    # Date sources
    metadata_date: datetime | None = None
    content_dates: list[datetime] = field(default_factory=list)
    semester_dates: list[str] = field(default_factory=list)


@dataclass
class AcademicClassification:
    """Academic content classification"""
    content_type: str = "general"  # course, research, policy, faculty, event, general
    confidence: float = 0.0

    # Type-specific indicators
    is_course_page: bool = False
    is_research_page: bool = False
    is_policy_page: bool = False
    is_faculty_page: bool = False

    course_codes: list[str] = field(default_factory=list)
    department: str | None = None
    semester: str | None = None

    # Scores for each type
    course_score: float = 0.0
    research_score: float = 0.0
    policy_score: float = 0.0
    faculty_score: float = 0.0


@dataclass
class LanguageInfo:
    """Multi-language detection information"""
    primary_language: str = "en"
    confidence: float = 0.0

    detected_languages: dict[str, float] = field(default_factory=dict)
    is_multilingual: bool = False

    # Content language distribution
    language_segments: list[tuple[str, int]] = field(default_factory=list)  # (lang, word_count)


class ContentAnalyzer:
    """Advanced content analysis for Stage 3 enrichment"""

    def __init__(self):
        """Initialize content analyzer"""
        self.stop_words = self._load_stop_words()

    def _load_stop_words(self) -> set[str]:
        """Load common English stop words"""
        return {
            'the', 'be', 'to', 'of', 'and', 'a', 'in', 'that', 'have', 'i',
            'it', 'for', 'not', 'on', 'with', 'he', 'as', 'you', 'do', 'at',
            'this', 'but', 'his', 'by', 'from', 'they', 'we', 'say', 'her', 'she',
            'or', 'an', 'will', 'my', 'one', 'all', 'would', 'there', 'their',
            'what', 'so', 'up', 'out', 'if', 'about', 'who', 'get', 'which', 'go'
        }

    def analyze_content_quality(
        self,
        text_content: str,
        html_structure: dict | None = None
    ) -> ContentQualityScore:
        """
        Analyze content quality and information density.

        Args:
            text_content: Extracted text content
            html_structure: Optional dict with HTML structure info
                           (headings, lists, tables, links, images counts)

        Returns:
            ContentQualityScore with detailed metrics
        """
        if not text_content or len(text_content.strip()) < 50:
            return ContentQualityScore(
                overall_score=0.0,
                information_density=0.0,
                is_substantive=False
            )

        # Basic text analysis
        sentences = self._split_sentences(text_content)
        paragraphs = self._split_paragraphs(text_content)
        words = text_content.lower().split()

        word_count = len(words)
        sentence_count = len(sentences)
        paragraph_count = len(paragraphs)

        # Calculate averages
        avg_sentence_length = word_count / max(sentence_count, 1)
        avg_paragraph_length = word_count / max(paragraph_count, 1)

        # Unique word ratio (vocabulary richness)
        unique_words = set(words)
        unique_word_ratio = len(unique_words) / max(word_count, 1)

        # Information density: ratio of content words to total words
        content_words = [w for w in words if w not in self.stop_words and len(w) > 3]
        information_density = len(content_words) / max(word_count, 1)

        # HTML structure analysis
        html_structure = html_structure or {}
        has_headings = html_structure.get('heading_count', 0) > 0
        has_lists = html_structure.get('list_count', 0) > 0
        has_tables = html_structure.get('table_count', 0) > 0
        link_count = html_structure.get('link_count', 0)
        image_count = html_structure.get('image_count', 0)

        # Calculate component scores

        # 1. Readability score (based on sentence length)
        # Ideal: 15-20 words per sentence
        readability = 100 - abs(avg_sentence_length - 17.5) * 2
        readability_score = max(0, min(100, readability))

        # 2. Structure score
        structure_score = 0
        if has_headings:
            structure_score += 30
        if has_lists:
            structure_score += 20
        if has_tables:
            structure_score += 20
        if paragraph_count >= 3:
            structure_score += 30

        # 3. Content substantiveness
        is_substantive = (
            word_count >= 100 and
            sentence_count >= 5 and
            information_density > 0.3
        )

        # 4. Navigation vs content page detection
        link_to_word_ratio = link_count / max(word_count, 1)
        is_navigation_page = link_to_word_ratio > 0.1 and word_count < 300

        # 5. Overall quality score
        overall_score = (
            information_density * 40 +  # Information density is most important
            (readability_score / 100) * 20 +
            (structure_score / 100) * 20 +
            (unique_word_ratio * 100) * 10 +
            (min(word_count / 500, 1) * 100) * 10  # Reward longer content
        )

        return ContentQualityScore(
            overall_score=round(overall_score, 2),
            information_density=round(information_density, 3),
            readability_score=round(readability_score, 2),
            structure_score=structure_score,
            word_count=word_count,
            sentence_count=sentence_count,
            paragraph_count=paragraph_count,
            avg_sentence_length=round(avg_sentence_length, 2),
            avg_paragraph_length=round(avg_paragraph_length, 2),
            unique_word_ratio=round(unique_word_ratio, 3),
            has_headings=has_headings,
            has_lists=has_lists,
            has_tables=has_tables,
            has_links=link_count,
            has_images=image_count,
            is_substantive=is_substantive,
            is_navigation_page=is_navigation_page,
        )

    def extract_recency_info(
        self,
        text_content: str,
        metadata: dict | None = None
    ) -> RecencyInfo:
        """
        Extract recency information and timestamps.

        Args:
            text_content: Page text content
            metadata: Optional metadata dict (may include published_date, modified_date)

        Returns:
            RecencyInfo with timestamp data
        """
        metadata = metadata or {}
        all_dates = []
        semester_dates = []

        # Extract dates from metadata
        metadata_date = None
        for key in ['modified_date', 'published_date', 'date', 'updated']:
            if key in metadata and metadata[key]:
                try:
                    if isinstance(metadata[key], datetime):
                        metadata_date = metadata[key]
                    else:
                        metadata_date = datetime.fromisoformat(str(metadata[key]))
                    all_dates.append(metadata_date)
                    break
                except (ValueError, TypeError):
                    pass

        # Extract dates from content
        content_dates = self._extract_dates_from_text(text_content)
        all_dates.extend(content_dates)

        # Extract semester dates
        semester_matches = re.findall(
            r'\b(Spring|Fall|Summer|Winter)\s+(\d{4})\b',
            text_content,
            re.IGNORECASE
        )
        semester_dates = [f"{season.title()} {year}" for season, year in semester_matches]

        # Analyze recency
        now = datetime.now()
        most_recent_date = max(all_dates) if all_dates else None
        oldest_date = min(all_dates) if all_dates else None

        days_since_update = None
        has_recent_content = False
        has_very_recent_content = False

        if most_recent_date:
            days_since_update = (now - most_recent_date).days
            has_recent_content = days_since_update <= 365
            has_very_recent_content = days_since_update <= 30

        # Freshness score (0-100)
        freshness_score = 0.0
        if days_since_update is not None:
            if days_since_update <= 30:
                freshness_score = 100
            elif days_since_update <= 90:
                freshness_score = 80
            elif days_since_update <= 365:
                freshness_score = 60
            elif days_since_update <= 730:
                freshness_score = 40
            else:
                freshness_score = 20

        return RecencyInfo(
            most_recent_date=most_recent_date,
            oldest_date=oldest_date,
            all_dates=all_dates,
            date_count=len(all_dates),
            has_recent_content=has_recent_content,
            has_very_recent_content=has_very_recent_content,
            days_since_update=days_since_update,
            metadata_date=metadata_date,
            content_dates=content_dates,
            semester_dates=semester_dates
        )

    def classify_academic_content(
        self,
        text_content: str,
        url: str,
        title: str | None = None
    ) -> AcademicClassification:
        """
        Classify academic content type (course, research, policy, etc.).

        Args:
            text_content: Page text content
            url: Page URL
            title: Optional page title

        Returns:
            AcademicClassification with content type and confidence
        """
        text_lower = text_content.lower()
        url_lower = url.lower()
        title_lower = (title or "").lower()

        # Combine all text for analysis
        all_text = f"{text_lower} {url_lower} {title_lower}"

        # Score each content type
        course_score = self._calculate_indicator_score(all_text, COURSE_INDICATORS)
        research_score = self._calculate_indicator_score(all_text, RESEARCH_INDICATORS)
        policy_score = self._calculate_indicator_score(all_text, POLICY_INDICATORS)
        faculty_score = self._calculate_indicator_score(all_text, FACULTY_INDICATORS)

        # URL-based boosting
        if '/course' in url_lower or '/class' in url_lower:
            course_score *= 1.5
        if '/research' in url_lower or '/publication' in url_lower:
            research_score *= 1.5
        if '/policy' in url_lower or '/regulation' in url_lower:
            policy_score *= 1.5
        if '/faculty' in url_lower or '/staff' in url_lower or '/people' in url_lower:
            faculty_score *= 1.5

        # Determine primary type
        scores = {
            'course': course_score,
            'research': research_score,
            'policy': policy_score,
            'faculty': faculty_score
        }

        content_type = max(scores, key=scores.get)
        confidence = scores[content_type]

        # If no strong signal, classify as general
        if confidence < 2.0:
            content_type = "general"
            confidence = 0.0
        else:
            # Normalize confidence to 0-1
            confidence = min(confidence / 10.0, 1.0)

        # Extract course codes
        course_codes = re.findall(r'\b([A-Z]{2,4})\s*(\d{3,4})\b', text_content)
        course_codes = [f"{dept}{num}" for dept, num in course_codes[:10]]  # Limit to 10

        # Extract department (basic heuristic)
        department = None
        dept_match = re.search(r'Department of ([A-Z][a-z]+(?: [A-Z][a-z]+)*)', text_content)
        if dept_match:
            department = dept_match.group(1)

        # Extract semester
        semester = None
        semester_match = re.search(
            r'\b(Spring|Fall|Summer|Winter)\s+(\d{4})\b',
            text_content,
            re.IGNORECASE
        )
        if semester_match:
            semester = f"{semester_match.group(1).title()} {semester_match.group(2)}"

        return AcademicClassification(
            content_type=content_type,
            confidence=round(confidence, 3),
            is_course_page=(content_type == "course"),
            is_research_page=(content_type == "research"),
            is_policy_page=(content_type == "policy"),
            is_faculty_page=(content_type == "faculty"),
            course_codes=course_codes,
            department=department,
            semester=semester,
            course_score=round(course_score, 2),
            research_score=round(research_score, 2),
            policy_score=round(policy_score, 2),
            faculty_score=round(faculty_score, 2)
        )

    def detect_language(self, text_content: str) -> LanguageInfo:
        """
        Detect primary language and identify multilingual content.

        Uses character-based heuristics and common word patterns.
        For production use, integrate langdetect or similar library.

        Args:
            text_content: Page text content

        Returns:
            LanguageInfo with language detection results
        """
        if not text_content or len(text_content.strip()) < 50:
            return LanguageInfo(primary_language="unknown", confidence=0.0)

        # Simple heuristic-based detection
        # In production, use: from langdetect import detect_langs

        # Character-based detection
        text_sample = text_content[:1000]  # Sample first 1000 chars

        # Check for non-Latin scripts
        has_cyrillic = bool(re.search(r'[А-Яа-я]', text_sample))
        has_chinese = bool(re.search(r'[\u4e00-\u9fff]', text_sample))
        has_arabic = bool(re.search(r'[\u0600-\u06ff]', text_sample))
        has_devanagari = bool(re.search(r'[\u0900-\u097f]', text_sample))

        if has_chinese:
            return LanguageInfo(primary_language="zh", confidence=0.9)
        if has_cyrillic:
            return LanguageInfo(primary_language="ru", confidence=0.8)
        if has_arabic:
            return LanguageInfo(primary_language="ar", confidence=0.8)
        if has_devanagari:
            return LanguageInfo(primary_language="hi", confidence=0.8)

        # For Latin scripts, check common words
        words = text_content.lower().split()[:200]
        word_freq = Counter(words)

        # English indicators
        english_words = {'the', 'and', 'is', 'to', 'of', 'in', 'for', 'on', 'with'}
        english_count = sum(word_freq.get(w, 0) for w in english_words)

        # Spanish indicators
        spanish_words = {'el', 'la', 'de', 'que', 'y', 'en', 'los', 'las', 'del'}
        spanish_count = sum(word_freq.get(w, 0) for w in spanish_words)

        # French indicators
        french_words = {'le', 'la', 'de', 'et', 'les', 'un', 'une', 'des', 'en'}
        french_count = sum(word_freq.get(w, 0) for w in french_words)

        # Determine primary language
        lang_scores = {
            'en': english_count,
            'es': spanish_count,
            'fr': french_count
        }

        primary_lang = max(lang_scores, key=lang_scores.get)
        total_indicators = sum(lang_scores.values())
        confidence = lang_scores[primary_lang] / max(total_indicators, 1)

        # Check if multilingual
        non_zero_langs = [lang for lang, count in lang_scores.items() if count > 0]
        is_multilingual = len(non_zero_langs) > 1 and confidence < 0.7

        return LanguageInfo(
            primary_language=primary_lang,
            confidence=round(confidence, 3),
            detected_languages={k: round(v / max(total_indicators, 1), 3)
                              for k, v in lang_scores.items() if v > 0},
            is_multilingual=is_multilingual
        )

    def _split_sentences(self, text: str) -> list[str]:
        """Split text into sentences"""
        # Simple sentence splitter
        sentences = re.split(r'[.!?]+\s+', text)
        return [s.strip() for s in sentences if s.strip()]

    def _split_paragraphs(self, text: str) -> list[str]:
        """Split text into paragraphs"""
        paragraphs = re.split(r'\n\s*\n', text)
        return [p.strip() for p in paragraphs if p.strip()]

    def _extract_dates_from_text(self, text: str) -> list[datetime]:
        """Extract dates from text content"""
        dates = []

        for pattern, date_format in DATE_PATTERNS:
            if date_format == 'relative':
                # Handle relative dates
                if 'today' in text.lower():
                    dates.append(datetime.now())
                if 'yesterday' in text.lower():
                    dates.append(datetime.now() - timedelta(days=1))

                # Handle "X days/weeks/months/years ago"
                relative_matches = re.findall(
                    r'(\d+)\s+(day|week|month|year)s?\s+ago',
                    text.lower()
                )
                for amount, unit in relative_matches:
                    amount = int(amount)
                    if unit == 'day':
                        dates.append(datetime.now() - timedelta(days=amount))
                    elif unit == 'week':
                        dates.append(datetime.now() - timedelta(weeks=amount))
                    elif unit == 'month':
                        dates.append(datetime.now() - timedelta(days=amount*30))
                    elif unit == 'year':
                        dates.append(datetime.now() - timedelta(days=amount*365))

            elif date_format == 'semester':
                # Handle semester dates (convert to approximate datetime)
                semester_matches = re.findall(pattern, text, re.IGNORECASE)
                for season, year in semester_matches:
                    try:
                        year = int(year)
                        # Approximate semester start dates
                        if season.lower() == 'spring':
                            dates.append(datetime(year, 1, 15))
                        elif season.lower() == 'summer':
                            dates.append(datetime(year, 6, 1))
                        elif season.lower() == 'fall':
                            dates.append(datetime(year, 9, 1))
                        elif season.lower() == 'winter':
                            dates.append(datetime(year, 12, 15))
                    except ValueError:
                        pass

            else:
                # Handle explicit date formats
                matches = re.findall(pattern, text)
                for match in matches:
                    try:
                        if isinstance(match, tuple):
                            date_str = ' '.join(str(m) for m in match)
                        else:
                            date_str = match

                        parsed_date = datetime.strptime(date_str, date_format)
                        # Only include reasonable dates (1990-2050)
                        if 1990 <= parsed_date.year <= 2050:
                            dates.append(parsed_date)
                    except (ValueError, TypeError):
                        pass

        return dates

    def _calculate_indicator_score(self, text: str, indicators: list[str]) -> float:
        """Calculate score based on indicator patterns"""
        score = 0.0
        for pattern in indicators:
            matches = re.findall(pattern, text, re.IGNORECASE)
            score += len(matches)
        return score
