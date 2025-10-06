"""Stage 3 Asynchronous Worker
Similarity detection & summarization for quality documents.
Uses datasketch MinHash for deduplication and BART for summarization.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any

from datasketch import MinHash, MinHashLSH

from src.common.delta_lake import get_delta_manager

logger = logging.getLogger(__name__)


class Stage3Worker:
    """Async worker for Stage 3 similarity detection & summarization."""

    def __init__(self, max_concurrent: int = 20, batch_size: int = 50):
        """Initialize Stage 3 worker.

        Args:
            max_concurrent: Max concurrent summarization tasks
            batch_size: Number of documents to process per batch

        """
        self.max_concurrent = max_concurrent
        self.batch_size = batch_size
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.delta = get_delta_manager()

        # Similarity threshold (0-1, lower = more similar)
        self.SIMILARITY_THRESHOLD = 0.3

    async def run(self):
        """Main worker loop - process quality documents."""
        logger.info(f"Stage 3 Worker starting with {self.max_concurrent} concurrent workers")

        # Read quality documents from stage2_page_analysis
        all_docs = self.delta.read('stage2_page_analysis')

        if not all_docs:
            logger.warning("No documents found in stage2_page_analysis")
            return

        # Filter to quality documents only (not low quality, not massive, not errors)
        quality_docs = [
            doc for doc in all_docs
            if not doc.get('is_low_quality', True)
            and not doc.get('is_massive_doc', False)
            and not doc.get('has_error', False)
            and doc.get('text_content')
        ]

        logger.info(f"Found {len(quality_docs)} quality documents to process (out of {len(all_docs)} total)")

        if not quality_docs:
            logger.info("No quality documents to process")
            return

        # Check already processed
        try:
            processed = self.delta.read('stage4_summaries')
            processed_hashes = {r['url_hash'] for r in processed}
        except Exception:
            processed_hashes = set()

        # Filter to pending
        pending = [doc for doc in quality_docs if doc.get('url_hash') not in processed_hashes]

        logger.info(f"Found {len(pending)} pending documents to summarize")

        if not pending:
            logger.info("No pending documents to process")
            return

        # Step 1: Similarity detection using MinHash LSH
        unique_docs = await self._deduplicate_documents(pending)
        logger.info(f"After deduplication: {len(unique_docs)} unique documents")

        # Step 2: Summarize unique documents
        for i in range(0, len(unique_docs), self.batch_size):
            batch = unique_docs[i:i + self.batch_size]
            logger.info(f"Summarizing batch {i // self.batch_size + 1}: {len(batch)} documents")

            # Process batch concurrently
            tasks = [self._summarize_document(doc) for doc in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Filter successful results
            valid_results = [r for r in results if isinstance(r, dict) and not isinstance(r, Exception)]

            # Save to Delta Lake
            if valid_results:
                self.delta.write('stage4_summaries', valid_results, mode='append', async_write=False)
                logger.info(f"Saved {len(valid_results)} summaries")

        logger.info("Stage 3 Worker completed all batches")

    async def _deduplicate_documents(self, documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Use MinHash LSH to detect and remove near-duplicate documents.

        Returns:
            List of unique documents

        """
        logger.info(f"Running similarity detection on {len(documents)} documents")

        # Create MinHash LSH index
        lsh = MinHashLSH(threshold=self.SIMILARITY_THRESHOLD, num_perm=128)

        unique_docs = []
        seen_similar = set()

        for doc in documents:
            url_hash = doc.get('url_hash')
            text = doc.get('text_content', '')

            if not text or url_hash in seen_similar:
                continue

            # Create MinHash for this document
            minhash = MinHash(num_perm=128)

            # Tokenize text into words
            words = text.lower().split()
            for word in words[:1000]:  # Limit to first 1000 words for efficiency
                minhash.update(word.encode('utf-8'))

            # Query LSH for similar documents
            similar = lsh.query(minhash)

            if similar:
                # Found similar document(s) - skip this one
                logger.debug(f"Skipping duplicate: {doc.get('url', '')[:80]}")
                seen_similar.add(url_hash)
                continue

            # No similar documents - add to index and results
            lsh.insert(url_hash, minhash)
            unique_docs.append(doc)

        logger.info(f"Deduplication complete: {len(unique_docs)} unique out of {len(documents)}")
        return unique_docs

    async def _summarize_document(self, doc: dict[str, Any]) -> dict[str, Any]:
        """Summarize single document using BART."""
        url = doc.get('url')
        url_hash = doc.get('url_hash')
        text = doc.get('text_content', '')

        async with self.semaphore:
            try:
                # Run summarization in thread pool (CPU-bound)
                loop = asyncio.get_event_loop()
                summary = await loop.run_in_executor(None, self._generate_summary_sync, text)

                # Extract key facts
                key_facts = self._extract_key_facts(text, doc.get('keywords', []))

                result = {
                    'url': url,
                    'url_hash': url_hash,
                    'title': doc.get('title', 'Untitled'),
                    'summary': summary,
                    'key_facts': key_facts,
                    'keywords': doc.get('keywords', []),
                    'word_count': doc.get('word_count', 0),
                    'quality_score': doc.get('quality_score', 0),
                    'summarized_at': datetime.now().isoformat(),
                }

                logger.info(f"Summarized: {url[:80]}")
                return result

            except Exception as e:
                logger.error(f"Failed to summarize {url}: {e}")
                return {
                    'url': url,
                    'url_hash': url_hash,
                    'title': doc.get('title', 'Error'),
                    'summary': 'Summarization failed',
                    'has_error': True,
                    'error_message': str(e),
                    'summarized_at': datetime.now().isoformat(),
                }

    def _generate_summary_sync(self, text: str, max_length: int = 150) -> str:
        """Generate summary using DistilBART (synchronous, CPU-bound).

        Args:
            text: Input text to summarize
            max_length: Max summary length in tokens

        Returns:
            Summary text

        """
        try:
            from transformers import pipeline

            # Use lightweight DistilBART for fast summarization
            summarizer = pipeline(
                "summarization",
                model="sshleifer/distilbart-cnn-12-6",
                device=-1  # CPU
            )

            # Limit input (BART has 1024 token limit)
            max_input = 1024
            if len(text.split()) > max_input:
                text = ' '.join(text.split()[:max_input])

            result = summarizer(
                text,
                max_length=max_length,
                min_length=30,
                do_sample=False
            )

            return result[0]['summary_text']

        except ImportError:
            logger.warning("Transformers not installed, using fallback summarization")
            return self._fallback_summary(text)
        except Exception as e:
            logger.error(f"BART summarization failed: {e}")
            return self._fallback_summary(text)

    def _fallback_summary(self, text: str, max_chars: int = 500) -> str:
        """Fallback summary - first N characters."""
        if len(text) <= max_chars:
            return text

        # Try to end at sentence boundary
        truncated = text[:max_chars]
        last_period = truncated.rfind('.')

        if last_period > max_chars * 0.7:  # If we find a period in last 30%
            return truncated[:last_period + 1]

        return truncated + "..."

    def _extract_key_facts(self, text: str, keywords: list[str]) -> list[str]:
        """Extract key facts from text using keywords.

        Returns:
            List of key fact sentences

        """
        sentences = text.split('.')
        key_facts = []

        # Find sentences containing keywords
        for sentence in sentences[:20]:  # First 20 sentences
            sentence = sentence.strip()
            if not sentence or len(sentence) < 20:
                continue

            # Check if sentence contains any keyword
            for keyword in keywords[:5]:  # Top 5 keywords
                if keyword.lower() in sentence.lower():
                    key_facts.append(sentence)
                    break

            if len(key_facts) >= 5:  # Max 5 key facts
                break

        # If no facts found, use first few sentences
        if not key_facts:
            key_facts = [s.strip() for s in sentences[:3] if s.strip() and len(s.strip()) > 20]

        return key_facts[:5]  # Max 5
