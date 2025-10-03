import hashlib
import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import scrapy
from scrapy.http import Response

from src.common.content_handlers import ContentTypeRouter
from src.common.nlp import (
    NLPSettings,
    classify_with_taxonomy,
    extract_content_tags,
    extract_entities_and_keywords,
    extract_glossary_terms,
    has_audio_links,
    initialize_nlp,
    load_glossary,
    load_taxonomy,
    summarize,
)
from src.common.schemas import EnrichmentItem
from src.common.urls import canonicalize_url_simple


class EnrichmentSpider(scrapy.Spider):
    """Stage 3 Enrichment Spider - reads validated URLs, collects content/metadata"""

    name = "enrichment"

    def __init__(
        self,
        predefined_tags: list[str] = None,
        urls_list: list[str] = None,
        urls_file: str = None,
        allowed_domains: list = None,
        headless_browser_config: dict = None,
        content_types_config: dict = None,
        nlp_config: dict = None,
        validation_metadata: list[dict[str, Any]] | None = None,
        *args,
        **kwargs
    ):
        super().__init__(*args, **kwargs)

        # Initialize NLP with configuration
        if nlp_config:
            nlp_settings = NLPSettings(
                spacy_model=nlp_config.get('spacy_model', 'en_core_web_sm'),
                transformer_model=nlp_config.get('transformer_ner_model') if nlp_config.get('use_transformers') else None,
                summarizer_model=nlp_config.get('summarizer_model') if nlp_config.get('use_transformers') else None,
                preferred_device=nlp_config.get('device', 'auto')
            )
            initialize_nlp(nlp_settings)
            self.logger.info(f"NLP initialized with transformers: {nlp_config.get('use_transformers', False)}")
            self.use_transformer_ner = nlp_config.get('use_transformers', False)
            self.summary_max_length = nlp_config.get('summary_max_length', 150)
            self.summary_min_length = nlp_config.get('summary_min_length', 30)
        else:
            self.use_transformer_ner = False
            self.summary_max_length = 150
            self.summary_min_length = 30

        # Load allowed domains from configuration or use default
        if allowed_domains:
            if isinstance(allowed_domains, str):
                # Handle comma-separated string
                self.allowed_domains = [d.strip() for d in allowed_domains.split(',')]
            else:
                self.allowed_domains = allowed_domains

        self.predefined_tags = set(predefined_tags or [])
        self.urls_list = urls_list or []
        self.urls_file = urls_file
        self.processed_count = 0
        self.validation_lookup: dict[str, dict[str, Any]] = {}
        if validation_metadata:
            for entry in validation_metadata:
                url = entry.get('url')
                if url:
                    self.validation_lookup[url] = entry
        if self.validation_lookup:
            self.logger.info(f'Loaded {len(self.validation_lookup)} validation metadata records')

        # Headless browser configuration
        self.headless_browser_config = headless_browser_config or {}
        self.headless_browser_enabled = self.headless_browser_config.get('enabled', False)

        # Content types configuration
        self.content_types_config = content_types_config or {}

        # Initialize content type router for PDF/media handling
        if self.content_types_config:
            self.content_router = ContentTypeRouter(self.content_types_config)
            self.logger.info(f"Content router enabled for types: {self.content_router.enabled_types}")
        else:
            self.content_router = None

        self.logger.info(f"Allowed domains: {self.allowed_domains}")
        self.logger.info(f"Headless browser enabled: {self.headless_browser_enabled}")

        # Load URLs from file if provided
        if self.urls_file and Path(self.urls_file).exists():
            try:
                with open(self.urls_file) as f:
                    file_urls = json.load(f)
                    self.urls_list.extend(file_urls)
                    self.logger.info(f"Loaded {len(file_urls)} URLs from {self.urls_file}")
            except Exception as e:
                self.logger.error(f"Failed to load URLs from file {self.urls_file}: {e}")

        # Initialize HuggingFace model for link scoring (lazy loading for latency)
        self.embedding_model = None
        self.link_scorer = None
        self._hf_models_initialized = False

        # Load taxonomy and glossary
        self.taxonomy = load_taxonomy()
        self.glossary = load_glossary()

        num_categories = len(self.taxonomy.get("categories", []))
        num_glossary_terms = sum(len(terms) for terms in self.glossary.get("terms", {}).values())

        self.logger.info(f"Loaded taxonomy with {num_categories} categories")
        self.logger.info(f"Loaded glossary with {num_glossary_terms} UConn-specific terms")
        self.logger.info(f"Enrichment spider initialized with {len(self.predefined_tags)} predefined tags")
        self.logger.info(f"Enrichment spider will process {len(self.urls_list)} URLs from orchestrator")

    def _build_request_meta(self, url: str) -> dict[str, Any]:
        """Build request metadata including stage 2 validation context if available."""
        meta: dict[str, Any] = {}
        validation_data = self.validation_lookup.get(url)
        if validation_data:
            meta['validation_data'] = validation_data
            url_hash = validation_data.get('url_hash')
            if url_hash:
                meta['url_hash'] = url_hash
        return meta

    def start_requests(self) -> Iterator[scrapy.Request]:
        """Load validated URLs from orchestrator queue or Stage 2 file"""

        # Priority 1: Use URLs from orchestrator queue if provided
        if self.urls_list:
            self.logger.info(f"Loading {len(self.urls_list)} URLs from orchestrator queue")
            for url in self.urls_list:
                yield scrapy.Request(
                    url=url,
                    callback=self.parse,
                    meta=self._build_request_meta(url),
                    dont_filter=True,
                )
            return

        # Priority 2: Fallback to Stage 2 output file
        stage2_output = Path("data/processed/stage02/validation_output.jsonl")

        if not stage2_output.exists():
            self.logger.warning(f"Stage 2 output file not found: {stage2_output}")
            self.logger.warning("EnrichmentSpider requires validation results from Stage 2 or URLs from orchestrator")
            self.logger.warning("Run Stage 2 first, or use orchestrator queue integration")
            return

        self.logger.info(f"Loading validated URLs from {stage2_output}")

        with open(stage2_output, encoding='utf-8') as f:
            for line_no, line in enumerate(f, 1):
                try:
                    data = json.loads(line.strip())

                    # Only process valid URLs
                    if not data.get('is_valid', False):
                        continue

                    url = data.get('url', '')
                    if url and url.startswith('http'):
                        if url not in self.validation_lookup:
                            self.validation_lookup[url] = data
                        yield scrapy.Request(
                            url=url,
                            callback=self.parse,
                            meta=self._build_request_meta(url),
                            dont_filter=True,
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

    def _score_links_with_hf(self, links: list[str], text_content: str) -> list[float]:
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
        url_hash = response.meta.get('url_hash') or validation_data.get('url_hash')

        if not url_hash:
            normalized_url = canonicalize_url_simple(response.url)
            url_hash = hashlib.sha256(normalized_url.encode('utf-8')).hexdigest()

        response_content_type = response.headers.get('Content-Type', b'').decode('utf-8')
        content_type = validation_data.get('content_type', response_content_type)
        normalized_content_type = content_type.split(';')[0].strip().lower()
        status_code = validation_data.get('status_code', response.status)

        try:
            # Handle non-HTML content types (PDF, images, etc.)
            if self.content_router and normalized_content_type != 'text/html':
                if self.content_router.can_process(normalized_content_type):
                    self.logger.info(f"Processing {normalized_content_type} content: {response.url}")
                    content_data = self.content_router.process_content(
                        response.body, response.url, url_hash, normalized_content_type
                    )

                    # Create enrichment item from content data
                    yield EnrichmentItem(
                        url=response.url,
                        url_hash=url_hash,
                        title=content_data.get('metadata', {}).get('title', ''),
                        text_content=content_data.get('text_content', ''),
                        content_summary=content_data.get('text_content', '')[:500] if content_data.get('text_content') else '',
                        entities=[],
                        keywords=[],
                        content_tags=[],
                        has_pdf_links=False,
                        has_audio_links=False,
                        link_scores=[],
                        first_seen=validation_data.get('validated_at', datetime.now().isoformat()),
                        status_code=status_code,
                        content_type=content_type,
                        word_count=content_data.get('word_count', 0),
                        **{f'pdf_{k}': v for k, v in content_data.get('metadata', {}).items()}  # Prefix PDF metadata
                    )
                    return
                else:
                    self.logger.warning(f"Unsupported content type {normalized_content_type} for {response.url}")
                    return

            # HTML content processing (existing logic)
            # Extract text content from the page
            title = response.xpath('//title/text()').get(default='').strip()

            # IMPROVED TEXT EXTRACTION: Remove navigation, footer, header, and other non-content elements
            # This ensures we only process main content and avoid irrelevant text
            text_content = ' '.join(
                response.xpath('''
                    //body//text()[
                        normalize-space()
                        and not(ancestor::script)
                        and not(ancestor::style)
                        and not(ancestor::nav)
                        and not(ancestor::footer)
                        and not(ancestor::header)
                        and not(ancestor::*[@role="navigation"])
                        and not(ancestor::*[@role="menu"])
                        and not(ancestor::*[@role="menubar"])
                        and not(ancestor::*[@role="banner"])
                        and not(ancestor::*[@role="contentinfo"])
                        and not(ancestor::*[contains(@class, "nav")])
                        and not(ancestor::*[contains(@class, "menu")])
                        and not(ancestor::*[contains(@class, "footer")])
                        and not(ancestor::*[contains(@class, "header")])
                        and not(ancestor::*[contains(@class, "sidebar")])
                    ]
                ''').getall()
            ).strip()

            # Perform NLP analysis with configured backend
            backend = "transformer" if self.use_transformer_ner else "spacy"
            entities, keywords = extract_entities_and_keywords(text_content, backend=backend)

            # Extract UConn-specific glossary terms and add to keywords
            glossary_terms = extract_glossary_terms(text_content, self.glossary)

            # Merge keywords with glossary terms, prioritizing glossary
            combined_keywords = list(dict.fromkeys(glossary_terms + keywords))[:15]

            content_summary = summarize(
                text_content,
                max_length=self.summary_max_length,
                min_length=self.summary_min_length
            )

            # TAXONOMY-BASED CLASSIFICATION
            # Classify content using the comprehensive taxonomy
            taxonomy_results = classify_with_taxonomy(text_content, self.taxonomy, top_k=5)

            # Extract category labels for content_tags
            content_tags = [result["category_label"] for result in taxonomy_results]

            # Also add URL path-based tags if available
            url_path = urlparse(response.url).path
            url_tags = extract_content_tags(url_path, self.predefined_tags)
            content_tags = list(dict.fromkeys(content_tags + url_tags))[:10]  # Limit to 10 tags

            # Check for special content types
            links = response.xpath('//a/@href').getall()
            has_pdf_links = any('pdf' in link.lower() for link in links)
            has_audio = has_audio_links(links) or bool(response.xpath('//audio').get())

            # Optional: HuggingFace-assisted link scoring (lazy initialization)
            if len(links) > 0:
                self._initialize_hf_models()
                self._score_links_with_hf(links, text_content)

            # Create enrichment item with improved data
            item = EnrichmentItem(
                url=response.url,
                url_hash=url_hash,
                title=title,
                text_content=text_content[:20000],  # Limit text length
                word_count=len(text_content.split()) if text_content else 0,
                entities=entities,
                keywords=combined_keywords,  # Use combined keywords with glossary terms
                content_tags=content_tags,  # Use taxonomy-based tags
                has_pdf_links=has_pdf_links,
                has_audio_links=has_audio,
                status_code=status_code,
                content_type=content_type,
                enriched_at=datetime.now().isoformat(),
                content_summary=content_summary
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
