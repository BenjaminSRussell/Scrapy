import json
from datetime import datetime
from pathlib import Path
from typing import Iterator, List
from urllib.parse import urlparse

import scrapy
from scrapy.http import Response

from common.schemas import EnrichmentItem
from common.nlp import extract_entities_and_keywords, extract_content_tags, has_audio_links
from common.urls import canonicalize_and_hash


class EnrichmentSpider(scrapy.Spider):
    """Stage 3 Enrichment Spider - reads validated URLs, collects content/metadata"""

    name = "enrichment"
    allowed_domains = ["uconn.edu"]

    def __init__(self, predefined_tags: List[str] = None, urls_list: List[str] = None, urls_file: str = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.predefined_tags = set(predefined_tags or [])
        self.urls_list = urls_list or []
        self.urls_file = urls_file
        self.processed_count = 0

        # Load URLs from file if provided
        if self.urls_file and Path(self.urls_file).exists():
            try:
                with open(self.urls_file, 'r') as f:
                    file_urls = json.load(f)
                    self.urls_list.extend(file_urls)
                    self.logger.info(f"Loaded {len(file_urls)} URLs from {self.urls_file}")
            except Exception as e:
                self.logger.error(f"Failed to load URLs from file {self.urls_file}: {e}")

        # Initialize HuggingFace model for link scoring (lazy loading for latency)
        self.embedding_model = None
        self.link_scorer = None
        self._hf_models_initialized = False

        self.logger.info(f"Enrichment spider initialized with {len(self.predefined_tags)} predefined tags")
        self.logger.info(f"Enrichment spider will process {len(self.urls_list)} URLs from orchestrator")

    def start_requests(self) -> Iterator[scrapy.Request]:
        """Load validated URLs from orchestrator queue or Stage 2 file"""

        # Priority 1: Use URLs from orchestrator queue if provided
        if self.urls_list:
            self.logger.info(f"Loading {len(self.urls_list)} URLs from orchestrator queue")
            for url in self.urls_list:
                yield scrapy.Request(url=url, callback=self.parse)
            return

        # Priority 2: Fallback to Stage 2 output file
        stage2_output = Path("data/processed/stage02/validated_urls.jsonl")

        if not stage2_output.exists():
            self.logger.warning(f"Stage 2 output file not found: {stage2_output}")
            self.logger.warning("EnrichmentSpider requires validation results from Stage 2 or URLs from orchestrator")
            self.logger.warning("Run Stage 2 first, or use orchestrator queue integration")
            return

        self.logger.info(f"Loading validated URLs from {stage2_output}")

        with open(stage2_output, 'r', encoding='utf-8') as f:
            for line_no, line in enumerate(f, 1):
                try:
                    data = json.loads(line.strip())

                    # Only process valid URLs
                    if not data.get('is_valid', False):
                        continue

                    url = data.get('url', '')
                    if url and url.startswith('http'):
                        yield scrapy.Request(
                            url=url,
                            callback=self.parse,
                            meta={
                                'validation_data': data,
                                'url_hash': data.get('url_hash', '')
                            }
                        )

                except json.JSONDecodeError as e:
                    self.logger.error(f"Failed to parse JSON at line {line_no}: {e}")

    def _initialize_hf_models(self):
        """Lazy initialization of HuggingFace models to avoid startup latency"""
        if self._hf_models_initialized:
            return

        try:
            # Optional import - graceful fallback if dependencies not available
            from sentence_transformers import SentenceTransformer

            # Use lightweight model for fast link scoring
            # all-MiniLM-L6-v2 is ~80MB and ~10ms per batch on CPU
            self.embedding_model = SentenceTransformer('all-MiniLM-L6-v2')

            # Pre-compute embeddings for common academic content categories
            self.academic_categories = [
                "academic courses and curriculum",
                "research and publications",
                "faculty and staff information",
                "student services and resources",
                "admissions and enrollment",
                "campus events and news"
            ]
            self.category_embeddings = self.embedding_model.encode(self.academic_categories)

            self.logger.info("HuggingFace models initialized for link scoring")

        except ImportError:
            self.logger.warning("sentence-transformers not available, skipping HF link scoring")

        except Exception as e:
            self.logger.warning(f"Failed to initialize HuggingFace models: {e}")

        self._hf_models_initialized = True

    def _score_links_with_hf(self, links: List[str], text_content: str) -> List[float]:
        """Score links using HuggingFace embeddings for relevance

        Returns relevance scores (0-1) for links based on content similarity.
        Uses batching and caching to minimize latency impact.
        """
        if not self.embedding_model or not links:
            return [0.0] * len(links)

        try:
            # Extract link text/context (anchor text + surrounding context)
            link_contexts = []
            for link in links[:50]:  # Limit to top 50 links to control latency
                # In practice, you'd extract anchor text and surrounding context
                # For now, use the URL path as a simple proxy
                from urllib.parse import urlparse
                parsed = urlparse(link)
                context = parsed.path.replace('/', ' ').replace('-', ' ').replace('_', ' ')
                link_contexts.append(context)

            if not link_contexts:
                return [0.0] * len(links)

            # Batch encode link contexts (more efficient than one-by-one)
            link_embeddings = self.embedding_model.encode(link_contexts[:50])

            # Compute similarity with academic categories
            from sentence_transformers.util import cos_sim
            similarities = cos_sim(link_embeddings, self.category_embeddings)

            # Take max similarity across all categories as relevance score
            relevance_scores = similarities.max(dim=1).values.tolist()

            # Pad with zeros if we had more links than we processed
            while len(relevance_scores) < len(links):
                relevance_scores.append(0.0)

            return relevance_scores

        except Exception as e:
            self.logger.warning(f"HuggingFace link scoring failed: {e}")
            return [0.0] * len(links)

    def parse(self, response: Response) -> Iterator[EnrichmentItem]:
        """Parse response and extract content/metadata"""
        validation_data = response.meta.get('validation_data', {})
        url_hash = response.meta.get('url_hash', '')

        try:
            # Extract text content from the page
            title = response.xpath('//title/text()').get(default='').strip()

            # Extract body text (excluding scripts, styles, etc.)
            text_content = ' '.join(
                response.xpath('//body//text()[normalize-space() and not(ancestor::script) and not(ancestor::style)]').getall()
            ).strip()

            # Perform NLP analysis
            entities, keywords = extract_entities_and_keywords(text_content)

            # Extract content tags from URL path
            url_path = urlparse(response.url).path
            content_tags = extract_content_tags(url_path, self.predefined_tags)

            # Check for special content types
            links = response.xpath('//a/@href').getall()
            has_pdf_links = any('pdf' in link.lower() for link in links)
            has_audio = has_audio_links(links) or bool(response.xpath('//audio').get())

            # Optional: HuggingFace-assisted link scoring (lazy initialization)
            link_scores = []
            if len(links) > 0:
                self._initialize_hf_models()
                link_scores = self._score_links_with_hf(links, text_content)

            # Create enrichment item
            item = EnrichmentItem(
                url=response.url,
                url_hash=url_hash,
                title=title,
                text_content=text_content[:20000],  # Limit text length
                word_count=len(text_content.split()) if text_content else 0,
                entities=entities,
                keywords=keywords,
                content_tags=content_tags,
                has_pdf_links=has_pdf_links,
                has_audio_links=has_audio,
                status_code=response.status,
                content_type=response.headers.get('Content-Type', b'').decode('utf-8'),
                enriched_at=datetime.now().isoformat()
            )

            self.processed_count += 1
            if self.processed_count % 100 == 0:
                self.logger.info(f"Enriched {self.processed_count} pages")

            yield item

        except Exception as e:
            self.logger.error(f"Error enriching {response.url}: {e}")

    def closed(self, reason):
        """Called when spider closes"""
        self.logger.info(f"Enrichment spider closed: {reason}")
        self.logger.info(f"Total pages enriched: {self.processed_count}")