"""Stage 4: Large Document Processor
Handles heavyweight summarization of large documents using powerful LLMs.
Processes documents from stage4_large_docs queue.
"""

import logging
from datetime import datetime
from typing import Any

from src.common.delta_lake import get_delta_manager

logger = logging.getLogger(__name__)


class LargeDocProcessor:
    """Process large documents with heavyweight models."""

    def __init__(self, model_name: str = "facebook/bart-large-cnn"):
        self.delta = get_delta_manager()
        self.model_name = model_name
        self.summarizer = None

        # Chunk settings for very large docs
        self.CHUNK_SIZE = 5000  # characters per chunk
        self.OVERLAP = 500  # overlap between chunks

    def _load_model(self):
        """Lazy load the heavyweight model."""
        if self.summarizer is not None:
            return

        try:
            from transformers import pipeline

            logger.info(f"Loading heavyweight model: {self.model_name}")
            self.summarizer = pipeline(
                "summarization",
                model=self.model_name,
                device=-1  # CPU - change to 0 for GPU
            )
            logger.info("Model loaded successfully")
        except Exception as e:
            logger.error(f"Failed to load model: {e}")
            raise

    def process_queue(self):
        """Process all pending large documents in queue."""
        try:
            # Read pending documents
            all_docs = self.delta.read('stage4_large_docs')
            pending_docs = [d for d in all_docs if d.get('status') == 'pending']

            if not pending_docs:
                logger.info("No pending large documents to process")
                return

            logger.info(f"Processing {len(pending_docs)} large documents")

            # Load model once for all documents
            self._load_model()

            summaries = []
            processed_count = 0

            for doc in pending_docs:
                try:
                    summary = self._process_document(doc)
                    if summary:
                        summaries.append(summary)
                        processed_count += 1
                except Exception as e:
                    logger.error(f"Failed to process doc {doc.get('url')}: {e}")

            # Save summaries
            if summaries:
                self.delta.write('stage4_summaries', summaries, mode='append', async_write=False)
                logger.info(f"Saved {len(summaries)} summaries")

            # Update queue status
            self._update_queue_status(all_docs, pending_docs)

            logger.info(f"Completed processing {processed_count} large documents")

        except Exception as e:
            logger.error(f"Queue processing failed: {e}", exc_info=True)

    def _process_document(self, doc: dict[str, Any]) -> dict[str, Any] | None:
        """Process a single large document."""
        url = doc.get('url')
        text = doc.get('text', '')
        word_count = doc.get('word_count', 0)

        logger.info(f"Processing large doc ({word_count} words): {url[:80]}")

        # Split into chunks if necessary
        chunks = self._split_into_chunks(text)
        logger.info(f"Split into {len(chunks)} chunks")

        # Summarize each chunk
        chunk_summaries = []
        for i, chunk in enumerate(chunks):
            try:
                summary = self._summarize_chunk(chunk)
                if summary:
                    chunk_summaries.append(summary)
                    logger.debug(f"Summarized chunk {i+1}/{len(chunks)}")
            except Exception as e:
                logger.warning(f"Failed to summarize chunk {i}: {e}")

        if not chunk_summaries:
            logger.warning(f"No summaries generated for {url}")
            return None

        # Combine chunk summaries
        combined_summary = " ".join(chunk_summaries)

        # Final summarization if combined is still too long
        if len(combined_summary) > 1000:
            combined_summary = self._summarize_chunk(combined_summary[:5000])

        return {
            'url': url,
            'summary': combined_summary,
            'original_word_count': word_count,
            'chunk_count': len(chunks),
            'processed_at': datetime.now().isoformat(),
            'model_used': self.model_name,
        }

    def _split_into_chunks(self, text: str) -> list:
        """Split large text into overlapping chunks."""
        if len(text) <= self.CHUNK_SIZE:
            return [text]

        chunks = []
        start = 0

        while start < len(text):
            end = start + self.CHUNK_SIZE
            chunk = text[start:end]

            # Try to break at sentence boundary
            if end < len(text):
                last_period = chunk.rfind('.')
                if last_period > self.CHUNK_SIZE // 2:
                    end = start + last_period + 1
                    chunk = text[start:end]

            chunks.append(chunk.strip())
            start = end - self.OVERLAP

        return chunks

    def _summarize_chunk(self, text: str) -> str | None:
        """Summarize a single chunk of text."""
        if not text or len(text) < 100:
            return None

        try:
            # Limit to model's max input
            max_input = 1024
            if len(text) > max_input:
                text = text[:max_input]

            result = self.summarizer(
                text,
                max_length=150,
                min_length=30,
                do_sample=False
            )

            return result[0]['summary_text']

        except Exception as e:
            logger.error(f"Chunk summarization failed: {e}")
            # Fallback: extract first few sentences
            sentences = text.split('.')[:3]
            return '. '.join(sentences) + '.'

    def _update_queue_status(self, all_docs: list, processed_docs: list):
        """Update queue with completed status."""
        processed_urls = {d.get('url') for d in processed_docs}

        updated_queue = []
        for doc in all_docs:
            if doc.get('url') in processed_urls:
                doc['status'] = 'completed'
                doc['completed_at'] = datetime.now().isoformat()
            updated_queue.append(doc)

        self.delta.write('stage4_large_docs', updated_queue, mode='overwrite', async_write=False)
        logger.info(f"Updated queue status for {len(processed_urls)} documents")


def process_large_documents():
    """Convenience function to process large document queue."""
    processor = LargeDocProcessor()
    processor.process_queue()


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
    )
    process_large_documents()
