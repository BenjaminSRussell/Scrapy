"""Crawl-profile NetworkPolicy egress (#677).

Static checks always run. Helm render checks run when a helm binary is present.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "k8s" / "helm" / "scraping-pipeline"
VALUES = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
NETPOL = (CHART / "templates" / "networkpolicy.yaml").read_text(encoding="utf-8")
EXAMPLE = ROOT / "k8s" / "examples" / "networkpolicy-crawl-profile-egress.yaml"
README = (ROOT / "k8s" / "README.md").read_text(encoding="utf-8")
HELM = os.environ.get("HELM_BIN") or shutil.which("helm")


def test_profile_allowlist_defaults_off_and_ships_the_uconn_profile():
    policy = VALUES["networkPolicy"]
    assert policy["profileAllowlist"] is False
    assert policy["enabled"] is False
    profiles = policy["crawlProfiles"]
    assert profiles == [
        {"name": "uconn", "allowedDomains": ["uconn.edu"], "egressCIDRs": []}
    ]
    # The documentation range is named in a comment only. It is not an egress CIDR.
    rendered = yaml.safe_dump(policy)
    assert "203.0.113.0/24" not in rendered


def test_template_generates_cidr_egress_and_a_break_glass_annotation():
    assert "scraping-pipeline.crawlProfileEgressCIDRs" in NETPOL
    assert "networkPolicy.profileAllowlist" in NETPOL
    assert 'scraping-pipeline/break-glass: "false"' in NETPOL
    assert 'scraping-pipeline/break-glass: "true"' in NETPOL
    assert "scraping-pipeline/network-break-glass" in NETPOL
    assert "profile-{{ $p.name }}-domains" in NETPOL
    assert "UDP/TCP 53" in NETPOL
    assert "allowedDomains are not selectors" in NETPOL
    # Coarse public egress remains the default branch.
    scrape = NETPOL.split("-scrape-egress")[1].split("---")[0]
    assert "cidr: 0.0.0.0/0" in scrape
    assert "scrapeExcept" in scrape
    glass = NETPOL.split("-break-glass-egress")[1]
    assert "egress:" in glass and "- {}" in glass
    helpers = (CHART / "templates" / "_helpers.tpl").read_text(encoding="utf-8")
    assert "profileAllowlist=true but no crawlProfiles egressCIDRs" in helpers
    assert "NetworkPolicy cannot match a DNS name" in helpers


def test_example_policy_documents_dns_and_break_glass():
    docs = [d for d in yaml.safe_load_all(EXAMPLE.read_text(encoding="utf-8")) if d]
    by_name = {d["metadata"]["name"]: d for d in docs}
    dns = by_name["example-allow-dns"]
    ports = {(p["protocol"], p["port"]) for p in dns["spec"]["egress"][0]["ports"]}
    assert ports == {("UDP", 53), ("TCP", 53)}
    assert dns["spec"]["egress"][0]["to"][0]["podSelector"]["matchLabels"]["k8s-app"] == "kube-dns"
    scrape = by_name["example-scrape-profile-egress"]
    assert scrape["metadata"]["annotations"]["scraping-pipeline/break-glass"] == "false"
    assert scrape["metadata"]["annotations"]["scraping-pipeline/profile-uconn-domains"] == "uconn.edu"
    block = scrape["spec"]["egress"][0]["to"][0]["ipBlock"]["cidr"]
    assert block == "203.0.113.0/24"
    glass = by_name["example-break-glass-egress"]
    assert glass["metadata"]["annotations"]["scraping-pipeline/break-glass"] == "true"
    assert glass["spec"]["podSelector"]["matchLabels"]["scraping-pipeline/network-break-glass"] == "true"
    assert glass["spec"]["egress"] == [{}]
    text = EXAMPLE.read_text(encoding="utf-8")
    assert "cannot match" in text
    assert "8.8.8.8" in text
    assert "kube-dns" in text
    assert "AAAA" in text or "IPv6" in text


def test_dns_egress_needs_and_break_glass_are_documented():
    assert "UDP and TCP 53" in README
    assert "kube-dns" in README
    assert "cannot match a hostname" in README
    assert "scraping-pipeline/break-glass" in README
    assert "network-break-glass" in README
    assert "profileAllowlist" in README
    assert "203.0.113.0/24" in README
    changelog = (ROOT.parent / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "#677" in changelog
    notes = (CHART / "templates" / "NOTES.txt").read_text(encoding="utf-8")
    assert "profileAllowlist" in notes
    assert "UDP/TCP 53" in notes


def _render(*sets: str) -> list[dict]:
    args = [HELM, "template", "t", str(CHART), "--set", "networkPolicy.enabled=true"]
    for s in sets:
        args += ["--set", s]
    out = subprocess.run(args, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    return [d for d in yaml.safe_load_all(out.stdout) if d]


@pytest.mark.skipif(HELM is None, reason="helm not installed")
def test_rendered_default_keeps_coarse_egress_and_annotates_break_glass():
    docs = _render()
    policies = [d for d in docs if d["kind"] == "NetworkPolicy"]
    scrape = next(d for d in policies if d["metadata"]["name"].endswith("-scrape-egress"))
    assert scrape["metadata"]["annotations"]["scraping-pipeline/break-glass"] == "false"
    assert scrape["metadata"]["annotations"]["scraping-pipeline/profile-uconn-domains"] == "uconn.edu"
    assert scrape["metadata"]["annotations"]["scraping-pipeline/profile-allowlist"] == "false"
    cidr = scrape["spec"]["egress"][0]["to"][0]["ipBlock"]
    assert cidr["cidr"] == "0.0.0.0/0"
    assert "10.0.0.0/8" in cidr["except"]
    glass = next(d for d in policies if d["metadata"]["name"].endswith("-break-glass-egress"))
    assert glass["metadata"]["annotations"]["scraping-pipeline/break-glass"] == "true"
    assert glass["spec"]["podSelector"]["matchLabels"]["scraping-pipeline/network-break-glass"] == "true"
    dns = next(d for d in policies if d["metadata"]["name"].endswith("-allow-dns"))
    ports = {(p["protocol"], p["port"]) for p in dns["spec"]["egress"][0]["ports"]}
    assert ("UDP", 53) in ports and ("TCP", 53) in ports


@pytest.mark.skipif(HELM is None, reason="helm not installed")
def test_rendered_profile_allowlist_uses_cidrs_and_rejects_an_empty_list():
    docs = _render(
        "networkPolicy.profileAllowlist=true",
        "networkPolicy.crawlProfiles[0].name=uconn",
        "networkPolicy.crawlProfiles[0].allowedDomains[0]=uconn.edu",
        "networkPolicy.crawlProfiles[0].allowedDomains[1]=www.uconn.edu",
        "networkPolicy.crawlProfiles[0].egressCIDRs[0]=203.0.113.0/24",
        "networkPolicy.crawlProfiles[0].egressCIDRs[1]=203.0.113.0/24",
    )
    scrape = next(
        d for d in docs if d["kind"] == "NetworkPolicy" and d["metadata"]["name"].endswith("-scrape-egress")
    )
    assert scrape["metadata"]["annotations"]["scraping-pipeline/profile-allowlist"] == "true"
    assert scrape["metadata"]["annotations"]["scraping-pipeline/profile-uconn-domains"] == "uconn.edu,www.uconn.edu"
    blocks = scrape["spec"]["egress"][0]["to"]
    assert blocks == [{"ipBlock": {"cidr": "203.0.113.0/24"}}]
    assert "0.0.0.0/0" not in yaml.safe_dump(scrape)
    bad = subprocess.run(
        [
            HELM,
            "template",
            "t",
            str(CHART),
            "--set",
            "networkPolicy.enabled=true",
            "--set",
            "networkPolicy.profileAllowlist=true",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert bad.returncode != 0
    assert "egressCIDRs" in bad.stderr
    host = subprocess.run(
        [
            HELM,
            "template",
            "t",
            str(CHART),
            "--set",
            "networkPolicy.enabled=true",
            "--set",
            "networkPolicy.crawlProfiles[0].name=uconn",
            "--set",
            "networkPolicy.crawlProfiles[0].allowedDomains[0]=https://uconn.edu/path",
            "--set",
            "networkPolicy.crawlProfiles[0].egressCIDRs[0]=203.0.113.0/24",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert host.returncode != 0
    assert "not a hostname" in host.stderr
