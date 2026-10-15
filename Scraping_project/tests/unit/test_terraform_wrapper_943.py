"""#943: Terraform wraps the Helm chart and does not grow a second deploy path.

Static checks only. They do not run Terraform or Helm and do not contact a cluster.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPO = ROOT.parent
MODULE = ROOT / "k8s" / "terraform" / "scraping-pipeline"
CHART = ROOT / "k8s" / "helm" / "scraping-pipeline"
DESIGN = REPO / "docs" / "product" / "943-terraform-cloud-deploy.md"

# Helm set names this wrapper must not override.
FORBIDDEN_SET_NAMES = (
    "REDIS_URL",
    "maxDeserializeFailures",
    "dlqTopic",
    "namespaceQuota",
    "QueueBacklog",
    "legendFormat",
)


def _module_text() -> str:
    parts = []
    for path in sorted(MODULE.glob("*")):
        if path.suffix in {".tf", ".md"} or path.name.endswith(".tfvars.example"):
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def test_design_and_deploy_guide_link_each_other():
    design = DESIGN.read_text(encoding="utf-8")
    deployment = (ROOT / "DEPLOYMENT.md").read_text(encoding="utf-8")
    readme = (MODULE / "README.md").read_text(encoding="utf-8")

    assert "../../Scraping_project/DEPLOYMENT.md" in design
    assert "k8s/terraform/scraping-pipeline/README.md" in design
    assert "k8s/terraform/scraping-pipeline/README.md" in deployment
    assert "../docs/product/943-terraform-cloud-deploy.md" in deployment
    assert "../../../DEPLOYMENT.md" in readme
    assert (CHART / "values.yaml").is_file()
    assert (CHART / "values-small.yaml").is_file()
    assert (CHART / "values-prod.yaml").is_file()


def test_module_uses_the_three_helm_profiles_and_no_cloud_cluster():
    text = _module_text()
    assert re.search(r"dev\s*=\s*\"\"", text)
    assert "values-small.yaml" in text
    assert "values-prod.yaml" in text
    assert "values.yaml" in text
    assert re.search(r'default\s*=\s*"small"', text)

    for banned in (
        "aws_eks_cluster",
        "aws_vpc",
        "google_container_cluster",
        "azurerm_kubernetes_cluster",
        "azurerm_resource_group",
    ):
        assert banned not in text

    for name in FORBIDDEN_SET_NAMES:
        assert not re.search(rf'name\s*=\s*"[^"]*{re.escape(name)}', text)


def test_redis_url_is_only_forbidden_and_state_is_gitignored():
    text = _module_text()
    for line in text.splitlines():
        if "REDIS_URL" not in line:
            continue
        assert re.search(r"do not|does not|must not", line, re.I), line

    gitignore = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert ".terraform/" in gitignore
    assert "*.tfstate" in gitignore
    example = (MODULE / "terraform.tfvars.example").read_text(encoding="utf-8")
    assert "Do not set REDIS_URL." in example
    assert not re.search(r"(?i)password\s*=", example)
    assert not (MODULE / "terraform.tfvars").exists()
