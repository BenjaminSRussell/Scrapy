#!/usr/bin/env python3
"""
Unified entry-point for starting the scraping pipeline locally or on Kubernetes.
"""

from __future__ import annotations

import os
import argparse
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Iterable, List, Sequence


REQUIRED_TOOLS = {
    "local": ("docker", "docker-compose"),
    "k8s": ("kubectl", "helm"),
}

LOCAL_GRAFANA_URL = "http://localhost:3000"
LOCAL_PROMETHEUS_URLS: List[str] = [
    "http://localhost:9091",
    "http://localhost:9097",
]
LOCAL_SEED_FILE = Path("data/raw/uconn_urls.csv")

DEFAULT_HELM_CHART = "k8s/helm/scraping-pipeline"
DEFAULT_HELM_VALUES = os.path.join(DEFAULT_HELM_CHART, "values.yaml")
PIPELINE_RELEASE = "scraping-pipeline"
PIPELINE_NAMESPACE = "scraping"
K8S_STAGE_DEFAULTS = {
    "stage1": {
        "release_suffix": "stage1",
        "namespace_suffix": "stage1",
        "set_overrides": (
            "stage2Worker.enabled=false",
            "stage3Worker.enabled=false",
        ),
    },
    "stage2": {
        "release_suffix": "stage2",
        "namespace_suffix": "stage2",
        "set_overrides": (
            "scrapyApp.enabled=false",
            "stage3Worker.enabled=false",
        ),
    },
    "stage3": {
        "release_suffix": "stage3",
        "namespace_suffix": "stage3",
        "set_overrides": (
            "scrapyApp.enabled=false",
            "stage2Worker.enabled=false",
        ),
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Start the scraping pipeline for local development or Kubernetes.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--env",
        choices=("local", "k8s"),
        default="local",
        help="Target environment to start. Use 'local' for docker-compose or 'k8s' for Helm on Kubernetes.",
    )
    parser.add_argument(
        "--wait-timeout",
        type=int,
        default=180,
        help="Seconds to wait for essential docker-compose services before running any optional post-start tasks.",
    )
    parser.add_argument(
        "--reset-delta",
        action="store_true",
        help="Reset Delta Lake tables and reload seed URLs after startup. Skipped by default.",
    )
    parser.add_argument(
        "--stage",
        choices=("pipeline", "stage1", "stage2", "stage3", "all-stages"),
        default="pipeline",
        help="For Kubernetes deployments, choose which portion of the pipeline to deploy.",
    )
    parser.add_argument(
        "--release",
        help="Overrides the Helm release name (only honored for single-stage deployments).",
    )
    parser.add_argument(
        "--release-prefix",
        default="scraping-pipeline",
        help="Base release name used when deploying multiple Kubernetes stages.",
    )
    parser.add_argument(
        "--namespace",
        help="Overrides the Kubernetes namespace (only honored for single-stage deployments).",
    )
    parser.add_argument(
        "--namespace-prefix",
        default="scraping",
        help="Base namespace prefix used when deploying multiple Kubernetes stages.",
    )
    parser.add_argument(
        "--chart",
        default=DEFAULT_HELM_CHART,
        help="Path to the Helm chart directory.",
    )
    parser.add_argument(
        "--values",
        default=DEFAULT_HELM_VALUES,
        help="Primary Helm values file applied to deployments.",
    )
    parser.add_argument(
        "--extra-values",
        action="append",
        default=[],
        metavar="FILE",
        help="Additional Helm values files (later files override earlier ones).",
    )
    parser.add_argument(
        "--set",
        dest="set_overrides",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Additional Helm --set overrides (may be supplied multiple times).",
    )
    return parser.parse_args()


def ensure_tools_available(env: str) -> None:
    missing = [tool for tool in REQUIRED_TOOLS[env] if shutil.which(tool) is None]
    if not missing:
        return

    joined_missing = ", ".join(missing)
    message = textwrap.dedent(
        f"""
        Missing required tooling for the '{env}' environment: {joined_missing}
        Please install the missing tool(s) and ensure they are available on your PATH before retrying.
        """
    ).strip()
    print(message, file=sys.stderr)
    sys.exit(1)


def run_command(command: Iterable[str], *, capture_output: bool = False) -> subprocess.CompletedProcess:
    cmd_list = list(command)
    try:
        return subprocess.run(
            cmd_list,
            check=True,
            capture_output=capture_output,
            text=capture_output,
        )
    except subprocess.CalledProcessError as exc:
        print(f"Command failed: {' '.join(cmd_list)}", file=sys.stderr)
        if exc.stdout:
            print(exc.stdout, file=sys.stderr)
        if exc.stderr:
            print(exc.stderr, file=sys.stderr)
        sys.exit(exc.returncode or 1)


def wait_for_exec(service: str, timeout: int) -> None:
    deadline = time.time() + timeout
    last_error = ""
    while time.time() < deadline:
        result = subprocess.run(
            ("docker-compose", "exec", "-T", service, "true"),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return
        last_error = result.stderr.strip() or result.stdout.strip()
        time.sleep(5)

    print(
        f"Service '{service}' did not become ready within {timeout} seconds.",
        file=sys.stderr,
    )
    if last_error:
        print(f"Last docker-compose exec error:\n{last_error}", file=sys.stderr)
    try:
        run_command(("docker-compose", "logs", "--tail", "50", service))
    finally:
        sys.exit(1)


def start_local(args: argparse.Namespace) -> None:
    print("Starting local environment with docker-compose...")
    run_command(("docker-compose", "up", "-d"))

    print(f"Waiting for 'postgres' service readiness (timeout={args.wait_timeout}s)...")
    wait_for_exec("postgres", args.wait_timeout)

    if args.reset_delta:
        if not LOCAL_SEED_FILE.exists():
            print(
                f"WARNING: Seed file not found at {LOCAL_SEED_FILE.resolve()}.\n"
                "Skipping Delta Lake reset. Populate data manually or provide the seed file.",
                file=sys.stderr,
            )
        else:
            print("Resetting Delta Lake via ephemeral Scrapy container...")
            run_command(
                (
                    "docker-compose",
                    "run",
                    "--rm",
                    "--no-deps",
                    "-T",
                    "scrapy-app",
                    "python",
                    "cli.py",
                    "reset",
                    "--force",
                )
            )
    else:
        print("Skipping Delta Lake reset. Use '--reset-delta' to wipe and reseed.")

    grafana_msg = f"Grafana dashboard: {LOCAL_GRAFANA_URL}"
    prometheus_msg = "Prometheus replicas: " + ", ".join(LOCAL_PROMETHEUS_URLS)
    print("Local environment started successfully!")
    print(grafana_msg)
    print(prometheus_msg)


def prompt_prerequisites() -> None:
    checklist = textwrap.dedent(
        """
        Mandatory checklist before continuing:
          1. Container images are built and pushed to the registry accessible by the cluster.
          2. Kubernetes secrets (including database credentials and API keys) are created.
          3. Target namespace exists and you have kubectl context set correctly.
          4. Helm values files are updated with the correct overrides for this deployment.
        """
    ).strip()
    print(checklist)
    confirmation = input("Type 'yes' to confirm that all prerequisites are satisfied: ").strip().lower()
    if confirmation != "yes":
        print("Aborting Kubernetes deployment. Please complete the prerequisites and try again.")
        sys.exit(0)


def ensure_files_exist(paths: Iterable[str]) -> None:
    missing = [path for path in paths if path and not os.path.exists(path)]
    if missing:
        print(f"Missing file(s): {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)


def deploy_helm_release(
    chart: str,
    release: str,
    namespace: str,
    values_files: Sequence[str],
    set_args: Sequence[str],
) -> None:
    if not os.path.isdir(chart):
        print(f"Helm chart not found at: {chart}", file=sys.stderr)
        sys.exit(1)
    ensure_files_exist(values_files)
    command: List[str] = [
        "helm",
        "upgrade",
        "--install",
        release,
        chart,
        "--namespace",
        namespace,
        "--create-namespace",
    ]
    for values_file in values_files:
        command.extend(["-f", values_file])
    for item in set_args:
        if item:
            command.extend(["--set", item])
    run_command(command)


def start_k8s(args: argparse.Namespace) -> None:
    prompt_prerequisites()
    if args.stage == "all-stages" and (args.release or args.namespace):
        print(
            "Note: '--release' and '--namespace' overrides are ignored when deploying multiple stages. "
            "Use '--release-prefix' and '--namespace-prefix' instead.",
            file=sys.stderr,
        )

    values_files = [args.values, *args.extra_values]
    additional_sets = args.set_overrides or []

    if args.stage == "pipeline":
        release = args.release or PIPELINE_RELEASE
        namespace = args.namespace or PIPELINE_NAMESPACE
        print(f"Deploying the full pipeline as Helm release '{release}' in namespace '{namespace}'...")
        deploy_helm_release(args.chart, release, namespace, values_files, additional_sets)
        print(f"Monitor pods with: kubectl get pods --namespace {namespace} --watch")
        return

    stages = ["stage1", "stage2", "stage3"] if args.stage == "all-stages" else [args.stage]
    for stage in stages:
        defaults = K8S_STAGE_DEFAULTS[stage]
        release = (
            args.release
            if args.stage != "all-stages" and args.release
            else f"{args.release_prefix}-{defaults['release_suffix']}"
        )
        namespace = (
            args.namespace
            if args.stage != "all-stages" and args.namespace
            else f"{args.namespace_prefix}-{defaults['namespace_suffix']}"
        )
        set_args = list(defaults.get("set_overrides", ()))
        set_args.extend(additional_sets)
        print(f"Deploying stage '{stage}' as Helm release '{release}' in namespace '{namespace}'...")
        deploy_helm_release(args.chart, release, namespace, values_files, set_args)
        print(f"Monitor pods with: kubectl get pods --namespace {namespace} --watch")


def main() -> None:
    args = parse_args()
    ensure_tools_available(args.env)

    if args.env == "local":
        start_local(args)
    else:
        start_k8s(args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:  # pylint: disable=broad-except
        print(f"Unexpected error: {exc}", file=sys.stderr)
        sys.exit(1)
