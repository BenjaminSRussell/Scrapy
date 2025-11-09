import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import asyncio
import unittest
from unittest.mock import MagicMock, patch

from src.common.constants import SUMMARY_LIMITS
from src.stage3.stage3_worker import Stage3Worker

LONG_TEXT = (
    "The James Webb Space Telescope (JWST) is a space telescope developed by NASA with contributions from the "
    "European Space Agency (ESA) and the Canadian Space Agency (CSA). It is intended to succeed the Hubble Space "
    "Telescope as NASA's flagship mission in astrophysics. JWST was launched on 25 December 2021 on an Ariane 5 "
    "rocket from Kourou, French Guiana, and arrived at the Sun–Earth L2 Lagrange point in January 2022. "
    "The first JWST image was released to the public via a press conference on 11 July 2022. The telescope is "
    "named after James E. Webb, who was the administrator of NASA from 1961 to 1968 and played an integral role "
    "in the Apollo program. The JWST's primary mirror consists of 18 hexagonal mirror segments made of gold-plated "
    "beryllium which combine to create a 6.5-meter (21 ft) diameter mirror—considerably larger than Hubble's 2.4 m "
    "(7.9 ft) mirror. Unlike the Hubble telescope, which observes in the near ultraviolet, visible, and near "
    "infrared (0.1 to 1 μm) spectra, the JWST observes in a lower frequency range, from long-wavelength visible "
    "light (red) through mid-infrared (0.6 to 28.3 μm). This will allow it to observe high redshift objects that "
    "are too old and too distant for Hubble to observe."
)

class TestSummarizationBoundaries(unittest.TestCase):

    def test_summarization_outputs_and_limits(self):
        mock_summarizer = MagicMock(
            return_value=[
                {
                    "summary_text": "The JWST is a large, infrared space telescope that was launched in 2021 to succeed the Hubble."
                }
            ]
        )
        mock_pipeline_func = MagicMock(return_value=mock_summarizer)
        mock_transformers = MagicMock()
        mock_transformers.pipeline = mock_pipeline_func

        with patch.dict(sys.modules, {"transformers": mock_transformers}):
            # --- Test Stage 4 (Abstractive) ---
            from src.stage4 import summarization

            s4_summary = summarization.summarize_with_heavy_model(LONG_TEXT)

        # --- Test Stage 3 (Extractive) ---
        async def run_stage3_test():
            worker = Stage3Worker()
            doc = {"text_content": LONG_TEXT}
            extractive_summary = await worker._summarize_document(doc)
            return extractive_summary["summary"]

        s3_summary = asyncio.run(run_stage3_test())

        # --- Assertions for Stage 4 ---
        self.assertFalse(
            s4_summary.startswith("The James Webb Space Telescope"),
            "Stage 4 summary should be abstractive and not start with the original text.",
        )
        self.assertEqual(
            s4_summary,
            "The JWST is a large, infrared space telescope that was launched in 2021 to succeed the Hubble.",
        )
        s4_word_count = len(s4_summary.split())
        self.assertLessEqual(
            s4_word_count,
            SUMMARY_LIMITS["max_length"],
            "Stage 4 summary should not exceed the max word length.",
        )
        self.assertGreaterEqual(
            s4_word_count,
            SUMMARY_LIMITS["min_length"] / 2,
            "Stage 4 summary should be close to the min word length.",
        )

        # --- Assertions for Stage 3 ---
        self.assertTrue(
            s3_summary.startswith("The James Webb Space Telescope"),
            "Stage 3 summary should be extractive and start with the original text.",
        )
        s3_sentence_count = len(s3_summary.split(".")) - 1
        self.assertLessEqual(
            s3_sentence_count,
            SUMMARY_LIMITS["extractive_max_sentences"],
            "Stage 3 summary should not exceed the max sentence limit.",
        )

if __name__ == "__main__":
    unittest.main()
