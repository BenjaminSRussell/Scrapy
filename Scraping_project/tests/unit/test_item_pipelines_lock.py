"""#679: regression lock for ITEM_PIPELINES order.

Settings are loaded the way production does (``get_project_settings()`` via
``scrapy.cfg`` -> ``src.settings``, as ``PipelineOrchestrator`` does), so a
config.yml ``scrapy.item_pipelines`` override is caught too. Any accidental
reorder, omission, addition or duplicate registration fails with a message
naming the pipeline and priority. Changing the pipeline chain on purpose
means updating EXPECTED here in the same PR.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from scrapy.utils.conf import build_component_list
from scrapy.utils.misc import load_object

PROJECT_ROOT = Path(__file__).resolve().parents[2]

EXPECTED: dict[str, int] = {
    "src.otel_tracing.OtelItemPipeline": 50,
    "src.pipelines.DataValidationPipeline": 100,
    "src.pipelines.DataCleansingPipeline": 150,
    "src.pipelines.QueueItemPipeline": 175,
    "src.pipelines.SchemaValidationPipeline": 200,
    "src.pipelines.MetadataPipeline": 250,
    "src.pipelines.RecencyScoringPipeline": 300,
    "src.pipelines.KafkaPipeline": 400,
    "src.pipelines.AggregationPipeline": 500,
    "src.pipelines.OffsiteCandidatePipeline": 800,
    "src.pipelines.GrafanaSummaryPipeline": 900,
}


def pipeline_diff(actual: dict[str, int], expected: dict[str, int]) -> list[str]:
    """Human-readable differences, one line per changed pipeline."""
    lines = []
    for name in sorted(set(expected) - set(actual)):
        lines.append(f"missing: {name} (expected priority {expected[name]})")
    for name in sorted(set(actual) - set(expected)):
        lines.append(f"unexpected: {name} at priority {actual[name]}")
    for name in sorted(set(actual) & set(expected)):
        if actual[name] != expected[name]:
            lines.append(f"priority changed: {name} {expected[name]} -> {actual[name]}")
    by_priority: dict[int, list[str]] = {}
    for name, prio in actual.items():
        by_priority.setdefault(prio, []).append(name)
    for prio, names in sorted(by_priority.items()):
        if len(names) > 1:
            lines.append(f"duplicate priority {prio}: {', '.join(sorted(names))} (order is ambiguous)")
    classes: dict[object, list[str]] = {}
    for name in actual:
        try:
            classes.setdefault(load_object(name), []).append(name)
        except Exception as exc:  # unimportable path is a regression too
            lines.append(f"unimportable: {name} ({type(exc).__name__}: {exc})")
    for names in classes.values():
        if len(names) > 1:
            lines.append(f"duplicate registration of one class: {', '.join(sorted(names))}")
    return lines


@pytest.fixture
def production_settings(monkeypatch):
    from scrapy.utils.project import get_project_settings

    monkeypatch.chdir(PROJECT_ROOT)
    monkeypatch.delenv("SCRAPY_SETTINGS_MODULE", raising=False)
    settings = get_project_settings()
    assert settings.get("BOT_NAME"), "src.settings was not loaded via scrapy.cfg"
    return settings


def test_item_pipelines_match_lock(production_settings):
    actual = {k: int(v) for k, v in production_settings.getdict("ITEM_PIPELINES").items() if v is not None}
    problems = pipeline_diff(actual, EXPECTED)
    assert not problems, "ITEM_PIPELINES changed:\n  " + "\n  ".join(problems)


def test_effective_order_scrapy_builds(production_settings):
    order = build_component_list(production_settings.getwithbase("ITEM_PIPELINES"))
    expected_order = [name for name, _ in sorted(EXPECTED.items(), key=lambda kv: kv[1])]
    assert [str(o) if isinstance(o, str) else f"{o.__module__}.{o.__qualname__}" for o in order] == expected_order


@pytest.mark.parametrize(
    ("mutate", "needle"),
    [
        (lambda d: d.update({"src.pipelines.KafkaPipeline": 120}), "priority changed: src.pipelines.KafkaPipeline 400 -> 120"),
        (lambda d: d.pop("src.pipelines.QueueItemPipeline"), "missing: src.pipelines.QueueItemPipeline (expected priority 175)"),
        (lambda d: d.update({"src.pipelines.MetadataPipeline": 300}), "duplicate priority 300"),
        (lambda d: d.update({"src.pipelines.NotARealPipeline": 600}), "unexpected: src.pipelines.NotARealPipeline at priority 600"),
    ],
    ids=["reorder", "omission", "ambiguous-order", "addition"],
)
def test_diff_names_the_changed_pipeline(mutate, needle):
    actual = dict(EXPECTED)
    mutate(actual)
    assert any(needle in line for line in pipeline_diff(actual, EXPECTED)), pipeline_diff(actual, EXPECTED)


def test_diff_detects_same_class_registered_twice(monkeypatch):
    import src.pipelines as pipelines

    monkeypatch.setattr(pipelines, "KafkaPipelineAlias", pipelines.KafkaPipeline, raising=False)
    actual = dict(EXPECTED, **{"src.pipelines.KafkaPipelineAlias": 450})
    lines = pipeline_diff(actual, EXPECTED)
    assert any("duplicate registration of one class" in line and "KafkaPipelineAlias" in line for line in lines), lines
