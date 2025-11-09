import asyncio
import logging
from datetime import datetime
from typing import Any

from src.common.delta_lake import get_delta_manager
from src.stage4.large_doc_processor import LargeDocProcessor

logger = logging.getLogger(__name__)

class Stage4Worker:

    def __init__(self, model_name: str = "facebook/bart-large-cnn"):
        self.delta = get_delta_manager()
        self.processor = LargeDocProcessor(model_name=model_name)

    async def run(self):
        logger.info("[STAGE4] Worker starting for large document processing")

        try:
            all_docs = self.delta.read("stage2_page_analysis")
        except Exception as e:
            logger.warning(f"[STAGE4] No documents found in stage2_page_analysis: {e}")
            return

        if not all_docs:
            logger.warning("[STAGE4] No documents found in stage2_page_analysis")
            return

        large_docs = [
            doc
            for doc in all_docs
            if doc.get("is_massive_doc", False)
            and not doc.get("has_error", False)
        ]

        logger.info(f"[STAGE4] Found {len(large_docs)} large documents to process")

        if not large_docs:
            logger.info("[STAGE4] No large documents to process")
            return

        try:
            processed = self.delta.read("stage4_large_doc_summaries")
            processed_urls = {r["url"] for r in processed}
        except Exception:
            processed_urls = set()

        pending = [doc for doc in large_docs if doc.get("url") not in processed_urls]

        if not pending:
            logger.info("[STAGE4] All large documents already processed")
            return

        logger.info(f"[STAGE4] Processing {len(pending)} pending large documents")

        results = []
        for i, doc in enumerate(pending):
            logger.info(f"[STAGE4] Processing {i+1}/{len(pending)}: {doc.get('url', '')[:80]}")

            try:
                result = await self._process_large_document(doc)
                if result:
                    results.append(result)
            except Exception as e:
                logger.error(f"[STAGE4] Failed to process {doc.get('url', '')}: {e}")

        if results:
            try:
                self.delta.write("stage4_large_doc_summaries", results, mode="append")
                logger.info(f"[STAGE4] ✅ Saved {len(results)} large document summaries")
            except Exception as e:
                logger.error(f"[STAGE4] Failed to save results: {e}")

        logger.info("[STAGE4] Worker completed")

    async def _process_large_document(self, doc: dict[str, Any]) -> dict[str, Any] | None:
        try:
            url = doc.get("url", "")
            url_hash = doc.get("url_hash", "")

            is_pdf = doc.get("content_hint") == "pdf"

            logger.info(f"[STAGE4] Fetching content from {url[:80]}")
            text, content_type = self.processor._fetch_content(url, is_pdf=is_pdf)

            if not text:
                logger.warning(f"[STAGE4] No text content for {url[:80]}")
                return None

            logger.info(f"[STAGE4] Processing {len(text)} characters")

            summary = self.processor.process_large_document(url, text)

            if not summary:
                logger.warning(f"[STAGE4] No summary generated for {url[:80]}")
                return None

            return {
                "url": url,
                "url_hash": url_hash,
                "summary": summary,
                "content_type": content_type,
                "original_size": len(text),
                "summary_size": len(summary),
                "compression_ratio": round(len(summary) / len(text), 3) if len(text) > 0 else 0,
                "processed_at": datetime.now().isoformat(),
            }

        except Exception as e:
            logger.error(f"[STAGE4] Error processing {doc.get('url', '')}: {e}")
            return None

async def run_stage4_worker():
    logger.info("[STAGE4] Worker starting in continuous mode...")

    while True:
        try:
            worker = Stage4Worker()
            await worker.run()

            logger.info("[STAGE4] Waiting 60 seconds before next check...")
            await asyncio.sleep(60)

        except KeyboardInterrupt:
            logger.info("[STAGE4] Worker shutting down...")
            break
        except Exception as e:
            logger.error(f"[STAGE4] Error in worker loop: {e}")
            await asyncio.sleep(30)

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    asyncio.run(run_stage4_worker())
