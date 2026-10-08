"""#514: PodDisruptionBudgets for the scraper, stage workers and Redis, sane for single replicas.

Static checks always run. The render checks run ``helm template`` when a helm binary
is available (``HELM_BIN`` or ``helm`` on PATH) and skip otherwise.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "k8s" / "helm" / "scraping-pipeline"
TEMPLATES = CHART / "templates"
PDB = (TEMPLATES / "poddisruptionbudgets.yaml").read_text(encoding="utf-8")
VALUES = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
HELM = os.environ.get("HELM_BIN") or shutil.which("helm")

WORKERS = {"scrapyApp": "scrapy", "stage2Worker": "stage2", "stage3Worker": "stage3", "stage4Worker": "stage4"}
STORES = {
    "postgresql": ("postgres", "postgresql-statefulset.yaml"),
    "redis": ("redis", "redis-statefulset.yaml"),
    "kafka": ("kafka", "kafka-statefulset.yaml"),
}


def test_values_define_worker_budget():
    pdb = VALUES["podDisruptionBudget"]
    assert pdb["enabled"] is True
    assert pdb["workers"]["maxUnavailable"] >= 1


@pytest.mark.parametrize(("values_key", "component"), sorted(WORKERS.items()))
def test_every_worker_has_a_toggled_pdb(values_key, component):
    assert f'(dict "values" .Values.{values_key} "component" "{component}")' in PDB


@pytest.mark.parametrize("store", sorted(STORES))
def test_store_replicas_in_template_match_statefulset(store):
    component, statefulset = STORES[store]
    declared = re.search(rf'"name" "{store}" "component" "{component}" "replicas" (\d+)', PDB)
    assert declared, f"{store} missing from the PDB stores list"
    actual = re.search(r"^  replicas: (\S.*?)\s*$", (TEMPLATES / statefulset).read_text(encoding="utf-8"), re.M)
    assert actual, f"{statefulset} has no replicas line"
    if actual.group(1).startswith("{{"):
        # Values-driven count (Kafka, #176): the PDB must take the maxUnavailable: 1
        # branch, which is safe for any broker count.
        assert declared.group(1) == "1", f"{store}: templated replicas must be declared 1 in the PDB list"
    else:
        assert actual.group(1) == declared.group(1), (
            f"{statefulset} replicas changed: update the PDB stores list (#514)"
        )


def test_networkpolicy_metrics_range_is_int():
    """Regression: sprig `add` returns int64 and `untilStep` needs int; every render failed."""
    text = (TEMPLATES / "networkpolicy.yaml").read_text(encoding="utf-8")
    assert "$metricsEnd := int (add (int .Values.scrapyApp.service.ports.metricsEnd) 1)" in text


# --- rendered (needs helm) ---------------------------------------------------

needs_helm = pytest.mark.skipif(not HELM, reason="helm not installed (set HELM_BIN to run render checks)")


def _render(*sets: str) -> list[dict]:
    args = [HELM, "template", "t", str(CHART)]
    for s in sets:
        args += ["--set", s]
    out = subprocess.run(args, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    return [d for d in yaml.safe_load_all(out.stdout) if d]


def _workloads(docs):
    return {
        d["metadata"]["name"]: (d["spec"].get("replicas", 1), d["spec"]["template"]["metadata"]["labels"])
        for d in docs
        if d["kind"] in ("Deployment", "StatefulSet")
    }


@needs_helm
def test_rendered_pdbs_select_exactly_one_workload_and_never_block_drains():
    docs = _render("stage4Worker.enabled=true")
    workloads = _workloads(docs)
    pdbs = [d for d in docs if d["kind"] == "PodDisruptionBudget"]
    components = set()
    for pdb in pdbs:
        sel = pdb["spec"]["selector"]["matchLabels"]
        matched = [n for n, (_, labels) in workloads.items() if all(labels.get(k) == v for k, v in sel.items())]
        assert len(matched) == 1, (pdb["metadata"]["name"], matched)
        replicas = workloads[matched[0]][0]
        spec = pdb["spec"]
        if "minAvailable" in spec:
            assert int(spec["minAvailable"]) < int(replicas), f"{pdb['metadata']['name']} blocks every eviction"
        else:
            assert int(spec["maxUnavailable"]) >= 1
        components.add(sel["app.kubernetes.io/component"])
    assert components == {"postgres", "redis", "kafka", "scrapy", "stage2", "stage3", "stage4"}


@needs_helm
def test_disabled_workloads_get_no_pdb():
    docs = _render("stage2Worker.enabled=false")  # stage4 is off by default
    names = {d["metadata"]["name"] for d in docs if d["kind"] == "PodDisruptionBudget"}
    assert not any(n.endswith(("-stage2-pdb", "-stage4-pdb")) for n in names), names
    assert any(n.endswith("-stage3-pdb") for n in names)


@needs_helm
def test_pdbs_can_be_disabled():
    docs = _render("podDisruptionBudget.enabled=false")
    assert not [d for d in docs if d["kind"] == "PodDisruptionBudget"]


@needs_helm
def test_network_policies_render_and_admit_stage4():
    docs = _render("stage4Worker.enabled=true", "networkPolicy.enabled=true")
    nps = [d for d in docs if d["kind"] == "NetworkPolicy"]
    assert nps
    for np in nps:
        exprs = list(np["spec"]["podSelector"].get("matchExpressions", []))
        for rule in np["spec"].get("ingress") or []:
            for src in rule.get("from", []):
                exprs += src.get("podSelector", {}).get("matchExpressions", [])
        for e in exprs:
            if "stage3" in e.get("values", []):
                assert "stage4" in e["values"], np["metadata"]["name"]
