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
        """Initialize Stage 3 worker."""
        self.max_concurrent = max_concurrent
        self.batch_size = batch_size
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.delta = get_delta_manager()
        self.SIMILARITY_THRESHOLD = 0.3

    async def run(self):
        """Main worker loop - process quality documents."""
        logger.info(f"Stage 3 Worker starting with {self.max_concurrent} concurrent workers")

        # Read quality documents from stage2_page_analysis
        all_docs = self.delta.read('stage2_page_analysis')

        if not all_docs:
            logger.warning("No documents found in stage2_page_analysis")
            return

        # Filter to quality documents only
        quality_docs = [
            doc for doc in all_docs
            if not doc.get('is_low_quality', True)
            and not doc.get('is_massive_doc', False)
            and not doc.get('has_error', False)
            and doc.get('text_content')
        ]

        logger.info(f"Found {len(quality_docs)} quality documents to process")

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

        if not pending:
            logger.info("All quality documents already processed")
            return

        logger.info(f"Processing {len(pending)} pending documents")

        # Process in batches
        for i in range(0, len(pending), self.batch_size):
            batch = pending[i:i + self.batch_size]
            logger.info(f"Processing batch {i // self.batch_size + 1}: {len(batch)} documents")

            # Deduplicate batch
            unique_batch = await self._deduplicate_documents(batch)
            logger.info(f"After deduplication: {len(unique_batch)} unique documents")

            # Summarize concurrently
            tasks = [self._summarize_document(doc) for doc in unique_batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Filter valid results
            valid_results = [r for r in results if isinstance(r, dict) and not isinstance(r, Exception)]

            if valid_results:
                self.delta.write('stage4_summaries', valid_results, mode='append', async_write=False)
                logger.info(f"Saved {len(valid_results)} summaries")

        logger.info("Stage 3 Worker completed all batches")

    async def _deduplicate_documents(self, documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Use MinHash LSH to detect and remove near-duplicate documents."""
        logger.info(f"Running similarity detection on {len(documents)} documents")

        lsh = MinHashLSH(threshold=self.SIMILARITY_THRESHOLD, num_perm=128)

        unique_docs = []
        seen_similar = set()

        for doc in documents:
            url_hash = doc.get('url_hash')
            text = doc.get('text_content', '')

            if not text or url_hash in seen_similar:
                continue

            # Create MinHash
            minhash = MinHash(num_perm=128)

            # Tokenize
            words = text.lower().split()
            for word in words[:1000]:  # First 1000 words
                minhash.update(word.encode('utf-8'))

            # Query LSH for similar documents
            similar = lsh.query(minhash)

            if similar:
                logger.debug(f"Skipping duplicate: {doc.get('url', '')[:80]}")
                seen_similar.add(url_hash)
                continue

            # Add to LSH
            lsh.insert(url_hash, minhash)
            unique_docs.append(doc)

        logger.info(f"Deduplication: {len(unique_docs)} unique out of {len(documents)}")
        return unique_docs

    async def _summarize_document(self, doc: dict[str, Any]) -> dict[str, Any]:
        """Summarize a single document."""
        async with self.semaphore:
            try:
                url = doc.get('url', '')
                text = doc.get('text_content', '')
                url_hash = doc.get('url_hash', '')

                # Simple extractive summary (first sentences)
                sentences = text.split('.')[:5]  # First 5 sentences
                summary = '. '.join(s.strip() for s in sentences if s.strip()) + '.'

                return {
                    'url': url,
                    'url_hash': url_hash,
                    'summary': summary,
                    'word_count': len(text.split()),
                    'keywords': doc.get('keywords', []),
                    'quality_score': doc.get('quality_score', 0),
                    'timestamp': datetime.now().isoformat()
                }

            except Exception as e:
                logger.error(f"Summarization failed for {doc.get('url', '')}: {e}")
                return None

    def _fallback_summary(self, text: str, max_chars: int = 500) -> str:
        """Fallback summary - first N characters."""
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + "..."

    def _extract_key_facts(self, text: str, keywords: list[str]) -> list[str]:
        """Extract key facts from text based on keywords."""
        sentences = text.split('.')
        facts = []

        for sentence in sentences[:20]:
            sentence = sentence.strip()
            if not sentence:
                continue

            # Check if sentence contains any keyword
            for keyword in keywords:
                if keyword.lower() in sentence.lower():
                    facts.append(sentence)
                    break

            if len(facts) >= 5:
                break

        return facts if facts else [s.strip() for s in sentences[:3] if s.strip()]
