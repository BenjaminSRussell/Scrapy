"""Automated regression tests for #154 (activity-log XSS + chart.resize on tab).

Runs the Node harness (stdlib unittest wrapper so CI/local work without
importing pytest at collection time when only `node` is required).
"""
from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
NODE_TEST = HERE / "test_activity_log_and_charts.mjs"


def _resolve_app_js() -> Path:
    env = os.environ.get("APP_JS")
    if env:
        return Path(env)
    candidates = [
        Path("/workspace/scrapy-154/dashboard/app.js"),
        HERE.parents[3] / "dashboard" / "app.js",  # Scraping_project/dashboard/app.js
        HERE.parents[2] / "dashboard" / "app.js",
    ]
    for c in candidates:
        if c.is_file():
            return c
    raise FileNotFoundError("dashboard/app.js not found; set APP_JS")


class TestActivityLogAndCharts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            subprocess.run(["node", "--version"], check=True, capture_output=True)
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            raise unittest.SkipTest("node is required for dashboard JS regressions") from exc

    def test_activity_log_xss_and_chart_resize_on_tab(self):
        app_js = _resolve_app_js()
        result = subprocess.run(
            ["node", str(NODE_TEST)],
            env={**os.environ, "APP_JS": str(app_js)},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=(result.stdout + "\n" + result.stderr),
        )
        self.assertIn("all #154 regression tests passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
