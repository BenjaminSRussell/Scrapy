"""#1567: every Secret the chart reads by default must be created by the k8s README Quick Start.

Static checks only (no helm needed): secret refs are collected from the templates, with
names and keys resolved through values.yaml, and compared with the README's
``kubectl create secret generic`` commands.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "k8s" / "helm" / "scraping-pipeline"
TEMPLATES = CHART / "templates"
VALUES = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
README = (ROOT / "k8s" / "README.md").read_text(encoding="utf-8")
SECRETS_TEMPLATE = (TEMPLATES / "secrets.yaml").read_text(encoding="utf-8")

VALUE_REF = re.compile(r"^\{\{-?\s*\.Values\.([\w.]+)\s*-?\}\}$")
SECRET_KEY_REF = re.compile(r"secretKeyRef:[ \t]*\n[ \t]*name:[ \t]*(.+?)[ \t]*\n[ \t]*key:[ \t]*(.+?)[ \t]*$", re.M)
SECRET_REF = re.compile(r"secretRef:[^\n]*\n[ \t]*name:[ \t]*(.+?)[ \t]*$", re.M)


def _resolve(raw: str) -> str:
    match = VALUE_REF.match(raw)
    if not match:
        return raw.strip("\"'")
    value = VALUES
    for part in match.group(1).split("."):
        value = value[part]
    return str(value)


def _template_refs() -> tuple[set[tuple[str, str]], set[str]]:
    """(secret name, key) pairs from secretKeyRef, and secret names from envFrom secretRef."""
    key_refs: set[tuple[str, str]] = set()
    whole_secrets: set[str] = set()
    for template in sorted(TEMPLATES.glob("*.yaml")):
        text = template.read_text(encoding="utf-8")
        for name, key in SECRET_KEY_REF.findall(text):
            key_refs.add((_resolve(name), _resolve(key)))
        for name in SECRET_REF.findall(text):
            whole_secrets.add(_resolve(name))
    return key_refs, whole_secrets


def _readme_secrets() -> dict[str, set[str]]:
    """Secret name -> keys, from the Quick Start's ``kubectl create secret generic`` commands."""
    quick_start = README.split("## Quick Start", 1)[1].split("## Directory Structure", 1)[0]
    secrets: dict[str, set[str]] = {}
    current = None
    for line in quick_start.splitlines():
        if "kubectl create secret generic" in line:
            current = line.split("kubectl create secret generic", 1)[1].split()[0]
            secrets.setdefault(current, set())
        if current:
            literal = re.search(r"--from-literal=([\w.-]+)=", line)
            if literal:
                secrets[current].add(literal.group(1))
            if not line.rstrip().endswith("\\"):
                current = None
    return secrets


KEY_REFS, WHOLE_SECRETS = _template_refs()
README_SECRETS = _readme_secrets()


def test_quick_start_parses_secrets():
    assert set(README_SECRETS) == {"postgres-credentials", "grafana-credentials", "redis-credentials"}


def test_templates_reference_known_secrets():
    assert KEY_REFS and WHOLE_SECRETS
    assert {name for name, _ in KEY_REFS} | WHOLE_SECRETS <= set(README_SECRETS)


def test_every_secret_key_ref_is_created_by_quick_start():
    missing = sorted((name, key) for name, key in KEY_REFS if key not in README_SECRETS.get(name, set()))
    assert not missing, f"README Quick Start does not create these secret keys: {missing}"


def test_every_envfrom_secret_is_created_by_quick_start():
    missing = sorted(WHOLE_SECRETS - set(README_SECRETS))
    assert not missing, f"README Quick Start does not create these secrets: {missing}"


def test_secrets_info_notes_list_every_referenced_key():
    missing = sorted(key for _, key in KEY_REFS if key not in SECRETS_TEMPLATE)
    assert not missing, f"secrets.yaml NOTES do not mention these keys: {missing}"
