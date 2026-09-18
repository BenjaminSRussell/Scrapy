"""Import smoke tests for Stage 1 BaseSpider path (#376 / #610)."""

import pytest


@pytest.mark.unit
@pytest.mark.stage1
def test_basespider_import_path():
    from src.stage1.base_spider import BaseSpider

    assert BaseSpider is not None
    assert BaseSpider.__name__ == "BaseSpider"


@pytest.mark.unit
@pytest.mark.stage1
def test_scout_and_experimental_spiders_import():
    from src.stage1.scout_spider import ScoutSpider
    from src.stage1.experimental.js_spider import JavaScriptSpider
    from src.stage1.experimental.deep_dive_spider import DeepDiveSpider
    from src.stage1.experimental.depth_spider import DepthSpider

    assert ScoutSpider is not None
    assert JavaScriptSpider is not None
    assert DeepDiveSpider is not None
    assert DepthSpider is not None
