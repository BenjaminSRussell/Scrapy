"""#504: Stage 4 in the Helm chart, toggled consistently by start.py --stage.

The chart gains a ``stage4Worker`` Deployment (off by default). Every stage preset
in start.py sets all four workload toggles, so each stage release runs exactly its
own workload, and ``all-stages`` includes Stage 4.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "k8s" / "helm" / "scraping-pipeline"
VALUES = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
TEMPLATE = (CHART / "templates" / "stage-workers-deployments.yaml").read_text(encoding="utf-8")
WORKLOADS = ("scrapyApp", "stage2Worker", "stage3Worker", "stage4Worker")
OWN = {"stage1": "scrapyApp", "stage2": "stage2Worker", "stage3": "stage3Worker", "stage4": "stage4Worker"}


@pytest.fixture()
def start(monkeypatch):
    monkeypatch.chdir(ROOT)
    spec = importlib.util.spec_from_file_location("_ops_start_504", ROOT / "start.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.shutil, "which", lambda tool: f"/usr/bin/{tool}")
    return module


def _block(name: str) -> str:
    """Top-level ``{{- if .Values.<name>.enabled }}`` ... column-0 ``{{- end }}`` block."""
    start = TEMPLATE.index(f"{{{{- if .Values.{name}.enabled }}}}")
    end = TEMPLATE.index("\n{{- end }}", start) + len("\n{{- end }}")
    return TEMPLATE[start:end] + "\n"


def test_stage4_values_off_by_default_and_complete():
    s4 = VALUES["stage4Worker"]
    assert s4["enabled"] is False
    assert s4["queueName"] == "stage4_large_docs"
    assert s4["replicaCount"] >= 1
    assert {"repository", "tag", "pullPolicy"} <= set(s4["image"])
    assert {"limits", "requests"} <= set(s4["resources"])
    assert s4["terminationGracePeriodSeconds"] >= VALUES["stage3Worker"]["terminationGracePeriodSeconds"]


def test_every_stage4_value_referenced_by_the_template_exists():
    refs = set(re.findall(r"\.Values\.stage4Worker\.([A-Za-z0-9_.]+)", TEMPLATE))
    assert refs, "template does not reference stage4Worker"
    for ref in refs:
        node = VALUES["stage4Worker"]
        for part in ref.split("."):
            assert isinstance(node, dict) and part in node, f"stage4Worker.{ref} missing from values.yaml"
            node = node[part]


def test_stage4_deployment_mirrors_stage3():
    """Same probes, preStop drain, env and volumes as Stage 3; only names differ."""
    s3, s4 = _block("stage3Worker"), _block("stage4Worker")
    expected = (
        s3.replace("stage3Worker", "stage4Worker")
        .replace("-stage3", "-stage4")
        .replace("component: stage3", "component: stage4")
        .replace("stage3-worker", "stage4-worker")
        .replace("src/stage3/stage3_worker.py", "src/stage4/stage4_worker.py")
        .replace("- stage3_worker.py", "- stage4_worker.py")
    )
    s4_no_comment = "\n".join(line for line in s4.splitlines() if not line.startswith("{{- /*")) + "\n"
    assert s4_no_comment == expected


def test_stage4_container_runs_the_stage4_script():
    s4 = _block("stage4Worker")
    assert 'command: ["python", "-u", "src/stage4/stage4_worker.py"]' in s4
    assert "- stage4_worker.py" in s4  # liveness probe matches the process
    assert 'if __name__ == "__main__":' in (ROOT / "src" / "stage4" / "stage4_worker.py").read_text()


@pytest.mark.parametrize("stage", ["stage1", "stage2", "stage3", "stage4"])
def test_each_stage_release_runs_exactly_its_own_workload(start, stage):
    effective = {w: bool(VALUES[w]["enabled"]) for w in WORKLOADS}
    overrides = start.K8S_STAGE_DEFAULTS[stage]["set_overrides"]
    for item in overrides:
        key, value = item.split("=")
        workload, field = key.split(".")
        assert field == "enabled" and workload in WORKLOADS
        effective[workload] = value == "true"
    assert {o.split(".")[0] for o in overrides} | {OWN[stage]} == set(WORKLOADS), "all four toggles must be explicit"
    assert {w for w, on in effective.items() if on} == {OWN[stage]}


def test_all_stages_includes_stage4(start):
    assert start.K8S_ALL_STAGES == ("stage1", "stage2", "stage3", "stage4")


def test_stage4_dry_run_plan(start, capsys):
    assert start.main(["--dry-run", "--env", "k8s", "--stage", "stage4"]) == 0
    out = capsys.readouterr().out
    assert (
        "helm upgrade --install scraping-pipeline-stage4 k8s/helm/scraping-pipeline --namespace scraping-stage4 "
        "--create-namespace -f k8s/helm/scraping-pipeline/values.yaml --set scrapyApp.enabled=false "
        "--set stage2Worker.enabled=false --set stage3Worker.enabled=false --set stage4Worker.enabled=true"
    ) in out


def test_all_stages_dry_run_deploys_four_releases(start, capsys):
    assert start.main(["--dry-run", "--env", "k8s", "--stage", "all-stages"]) == 0
    out = capsys.readouterr().out
    for stage in OWN:
        assert f"helm upgrade --install scraping-pipeline-{stage} " in out


def test_network_policies_admit_stage4_wherever_stage3_is_admitted():
    """Default-deny chart: a Stage 4 pod missing from an allowlist can't reach Redis/Postgres."""
    text = (CHART / "templates" / "networkpolicy.yaml").read_text(encoding="utf-8")
    lists = re.findall(r"values:\n((?:\s*- [\w-]+.*\n)+)", text)
    with_stage3 = [block for block in lists if re.search(r"- stage3\b", block)]
    assert len(with_stage3) >= 3  # core egress, redis ingress, postgres ingress
    for block in with_stage3:
        assert re.search(r"- stage4\b", block), block


def test_docs_cover_stage4_toggle():
    readme = (ROOT / "k8s" / "README.md").read_text(encoding="utf-8")
    assert "--set stage4Worker.enabled=true" in readme
    assert "| `stage4` | off | off | off | on |" in readme
