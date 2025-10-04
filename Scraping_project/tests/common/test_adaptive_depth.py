import unittest
from pathlib import Path
import os
import json
from Scraping_project.src.common.adaptive_depth import AdaptiveDepthManager, SectionStats

class TestSectionStats(unittest.TestCase):
    def setUp(self):
        self.stats = SectionStats(section_pattern="uconn.edu/admissions")

    def test_post_init(self):
        self.assertIsNotNone(self.stats.keyword_manager)

    def test_update_stats(self):
        self.stats.update_stats(discovered=10, validated=5, content_pages=2, avg_words=500, depth_reached=2)
        self.assertEqual(self.stats.total_urls_discovered, 10)
        self.assertEqual(self.stats.total_urls_validated, 5)
        self.assertEqual(self.stats.total_content_pages, 2)
        self.assertEqual(self.stats.avg_word_count, 500)
        self.assertEqual(self.stats.max_useful_depth, 2)
        self.assertAlmostEqual(self.stats.content_density, 0.5)

    def test_calculate_recommended_depth(self):
        self.stats.content_density = 0.9
        self.stats.avg_word_count = 1600
        self.stats.total_content_pages = 600
        self.assertGreater(self.stats.calculate_recommended_depth(), 3)

class TestAdaptiveDepth(unittest.TestCase):
    def setUp(self):
        self.config_file = Path("test_config.json")
        self.manager = AdaptiveDepthManager(config_file=self.config_file)

    def tearDown(self):
        if os.path.exists(self.config_file):
            os.remove(self.config_file)
        if os.path.exists(str(self.config_file) + ".lock"):
            os.remove(str(self.config_file) + ".lock")
        if os.path.exists(str(self.config_file) + ".tmp"):
            os.remove(str(self.config_file) + ".tmp")

    def test_extract_section(self):
        self.assertEqual(self.manager.extract_section("http://uconn.edu/admissions"), "uconn.edu/admissions")
        self.assertEqual(self.manager.extract_section("http://www.uconn.edu/admissions"), "uconn.edu/admissions")
        self.assertEqual(self.manager.extract_section("http://events.uconn.edu/admissions"), "events.uconn.edu")
        self.assertEqual(self.manager.extract_section("http://uconn.edu"), "uconn.edu")

    def test_record_discovery(self):
        self.manager.record_discovery("http://uconn.edu/admissions", 1)
        self.assertIn("uconn.edu/admissions", self.manager.section_stats)
        self.assertEqual(self.manager.section_stats["uconn.edu/admissions"].total_urls_discovered, 1)

    def test_record_validation(self):
        self.manager.record_validation("http://uconn.edu/admissions", is_valid=True, has_content=True, word_count=300, depth=1)
        self.assertIn("uconn.edu/admissions", self.manager.section_stats)
        stats = self.manager.section_stats["uconn.edu/admissions"]
        self.assertEqual(stats.total_urls_validated, 1)
        self.assertEqual(stats.total_content_pages, 1)
        self.assertEqual(stats.avg_word_count, 300)

    def test_get_depth_for_url(self):
        self.assertEqual(self.manager.get_depth_for_url("http://uconn.edu/admissions"), self.manager.base_depth)
        self.manager.record_validation("http://uconn.edu/admissions", is_valid=True, has_content=True, word_count=1600, depth=1)
        self.assertGreater(self.manager.get_depth_for_url("http://uconn.edu/admissions"), self.manager.base_depth)

    def test_save_and_load_config(self):
        self.manager.record_discovery("http://uconn.edu/admissions", 1)
        self.manager.save_config()
        new_manager = AdaptiveDepthManager(config_file=self.config_file)
        self.assertIn("uconn.edu/admissions", new_manager.section_stats)
        self.assertEqual(new_manager.section_stats["uconn.edu/admissions"].total_urls_discovered, 1)

    def test_get_high_value_sections(self):
        self.manager.record_validation("http://uconn.edu/admissions", is_valid=True, has_content=True, word_count=300, depth=1)
        self.manager.section_stats["uconn.edu/admissions"].total_content_pages = 60
        self.manager.section_stats["uconn.edu/admissions"].has_valuable_content = True
        high_value = self.manager.get_high_value_sections()
        self.assertIn("uconn.edu/admissions", high_value)

    def test_get_low_value_sections(self):
        self.manager.record_discovery("http://uconn.edu/admissions", 30)
        self.manager.record_validation("http://uconn.edu/admissions", is_valid=True, has_content=False)
        low_value = self.manager.get_low_value_sections()
        self.assertIn("uconn.edu/admissions", low_value)

    def test_print_report(self):
        self.manager.record_validation("http://uconn.edu/admissions", is_valid=True, has_content=True, word_count=300, depth=1)
        self.manager.print_report() # Just check that it runs without error

    def test_suggest_depth_adjustments(self):
        self.manager.record_validation("http://uconn.edu/admissions", is_valid=True, has_content=True, word_count=1600, depth=1)
        suggestions = self.manager.suggest_depth_adjustments()
        self.assertIn("uconn.edu/admissions", [s['section'] for s in suggestions['increase_depth']])

    def test_load_corrupted_config(self):
        with open(self.config_file, "w") as f:
            f.write("corrupted data")
        new_manager = AdaptiveDepthManager(config_file=self.config_file)
        self.assertEqual(len(new_manager.section_stats), 0)

    def test_save_locked_config(self):
        with open(str(self.config_file) + ".lock", "w") as f:
            f.write("")
        self.manager.record_discovery("http://uconn.edu/admissions", 1)
        self.manager.save_config()
        # Check that the config file was not written
        if os.path.exists(self.config_file):
            with open(self.config_file, "r") as f:
                self.assertEqual(f.read(), "")

if __name__ == "__main__":
    unittest.main()
