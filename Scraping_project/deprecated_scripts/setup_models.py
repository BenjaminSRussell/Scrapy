#!/usr/bin/env python3
"""Download and cache transformer models for Stage 3 and Stage 4.

This script pre-downloads the summarization models to avoid delays during pipeline execution.
Run this after installing dependencies and before running the pipeline.
"""

import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


def download_model(model_name: str, task: str = "summarization"):
    """Download and cache a transformer model.

    Args:
        model_name: HuggingFace model identifier
        task: Task type (summarization, text-generation, etc.)
    """
    try:
        from transformers import pipeline

        logger.info(f"Downloading model: {model_name}")
        logger.info(f"This may take several minutes depending on your connection...")

        # Load pipeline - this will download and cache the model
        pipe = pipeline(task, model=model_name, device=-1)

        logger.info(f"✅ Successfully downloaded and cached: {model_name}")

        # Clean up
        del pipe

        return True

    except ImportError:
        logger.error("❌ transformers library not installed. Run: pip install transformers torch")
        return False
    except Exception as e:
        logger.error(f"❌ Failed to download {model_name}: {e}")
        return False


def main():
    """Download all required models for the pipeline."""
    logger.info("=" * 80)
    logger.info("MODEL SETUP - Downloading Transformer Models")
    logger.info("=" * 80)

    models = [
        {
            "name": "sshleifer/distilbart-cnn-12-6",
            "purpose": "Stage 3 - Fast summarization (lightweight)",
            "size": "~350 MB"
        },
        {
            "name": "facebook/bart-large-cnn",
            "purpose": "Stage 4 - Heavy processing (large documents)",
            "size": "~1.6 GB"
        }
    ]

    logger.info(f"\nWill download {len(models)} models:")
    for i, model in enumerate(models, 1):
        logger.info(f"{i}. {model['name']}")
        logger.info(f"   Purpose: {model['purpose']}")
        logger.info(f"   Size: {model['size']}")

    logger.info(f"\nTotal download size: ~2 GB")
    logger.info(f"Models will be cached in: ~/.cache/huggingface/hub/")

    # Ask for confirmation
    try:
        response = input("\nProceed with download? [Y/n]: ").strip().lower()
        if response and response not in ['y', 'yes']:
            logger.info("Download cancelled.")
            return
    except (KeyboardInterrupt, EOFError):
        logger.info("\nDownload cancelled.")
        return

    logger.info("\nStarting downloads...\n")

    success_count = 0
    for i, model in enumerate(models, 1):
        logger.info(f"[{i}/{len(models)}] {model['name']}")
        if download_model(model['name']):
            success_count += 1
        logger.info("")

    logger.info("=" * 80)
    if success_count == len(models):
        logger.info(f"✅ SUCCESS - All {len(models)} models downloaded successfully!")
        logger.info("\nYou can now run the pipeline:")
        logger.info("  python run_full_pipeline_test.py")
    else:
        logger.warning(f"⚠️  Downloaded {success_count}/{len(models)} models")
        logger.warning("Some models failed to download. Check errors above.")
        sys.exit(1)
    logger.info("=" * 80)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("\n\nDownload interrupted by user.")
        sys.exit(1)
    except Exception as e:
        logger.error(f"\n\nUnexpected error: {e}", exc_info=True)
        sys.exit(1)
