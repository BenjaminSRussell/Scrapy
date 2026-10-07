#!/usr/bin/env python3
"""
Unified entry-point for starting the scraping pipeline locally or on Kubernetes.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import textwrap
import time
from collections.abc import Iterable, Sequence
from pathlib import Path

REQUIRED_TOOLS = {
    "local": ("docker", "docker-compose"),
    "k8s": ("kubectl", "helm"),
}

LOCAL_GRAFANA_URL = "http://localhost:3000"
LOCAL_PROMETHEUS_URLS: list[str] = [
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

    print("\n" + "=" * 70)
    print("Local Environment Started Successfully!")
    print("=" * 70)
    print(f"\n📊 Grafana Dashboard: {LOCAL_GRAFANA_URL}")
    print("   - Default credentials: admin / (password from .env GRAFANA_ADMIN_PASSWORD)")
    print("   - View real-time metrics, dashboards, and alerts")
    print("\n📈 Prometheus Replicas (HA Setup):")
    for idx, url in enumerate(LOCAL_PROMETHEUS_URLS, 1):
        print(f"   - Replica {idx}: {url}")
    print("\n📝 Viewing Logs:")
    print("   - All services:        docker-compose logs -f")
    print("   - Scrapy app:          docker-compose logs -f scrapy-app")
    print("   - Stage 2 worker:      docker-compose logs -f stage2-worker")
    print("   - Stage 3 worker:      docker-compose logs -f stage3-worker")
    print("   - Stage 4 worker:      docker-compose logs -f stage4-worker")
    print("   - Kafka:               docker-compose logs -f kafka")
    print("   - Redis:               docker-compose logs -f redis")
    print("   - PostgreSQL:          docker-compose logs -f postgres")
    print("   - Grafana:             docker-compose logs -f grafana")
    print("\n🔧 Other Useful Commands:")
    print("   - Check service status: docker-compose ps")
    print("   - Stop all services:    docker-compose down")
    print("   - Restart a service:    docker-compose restart <service-name>")
    print("   - View resource usage:  docker stats")
    print("=" * 70 + "\n")


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
    command: list[str] = [
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


def wait_for_pods_ready(namespace: str, timeout: int = 300) -> None:
    """Wait for all pods in namespace to be ready."""
    print(f"Waiting for pods in namespace '{namespace}' to be ready (timeout={timeout}s)...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = subprocess.run(
            (
                "kubectl",
                "get",
                "pods",
                "--namespace",
                namespace,
                "-o",
                "jsonpath={.items[*].status.conditions[?(@.type=='Ready')].status}",
            ),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            statuses = result.stdout.strip().split()
            if statuses and all(status == "True" for status in statuses):
                print(f"All pods in namespace '{namespace}' are ready!")
                return
        time.sleep(5)
    print(f"WARNING: Not all pods became ready within {timeout}s. Check status with: kubectl get pods -n {namespace}")


def verify_hpa_status(namespace: str) -> None:
    """Verify HPA status and display metrics."""
    print(f"\nChecking HorizontalPodAutoscalers in namespace '{namespace}'...")
    result = subprocess.run(
        ("kubectl", "get", "hpa", "--namespace", namespace),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0 and result.stdout.strip():
        print("HPA Status:")
        print(result.stdout)
    else:
        print("No HPAs found or error retrieving HPA status.")


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
        print("\nDeployment complete! Waiting for pods to be ready...")
        wait_for_pods_ready(namespace, timeout=300)
        verify_hpa_status(namespace)
        print(f"\n{'=' * 70}")
        print("Kubernetes Deployment Summary")
        print(f"{'=' * 70}")
        print(f"Release: {release}")
        print(f"Namespace: {namespace}")
        print("\n📊 Accessing Services:")
        print(f"   - Grafana:     kubectl port-forward -n {namespace} svc/{release}-grafana 3000:3000")
        print("                  Then open: http://localhost:3000")
        print(f"   - Prometheus:  kubectl port-forward -n {namespace} svc/{release}-prometheus 9090:9090")
        print("                  Then open: http://localhost:9090")
        print("\n📝 Viewing Logs:")
        print(f"   - Scrapy app:     kubectl logs -n {namespace} -l app.kubernetes.io/component=scrapy --tail=100 -f")
        print(
            f"   - Stage 2 worker: kubectl logs -n {namespace} -l app.kubernetes.io/component=stage2-worker --tail=100 -f"
        )
        print(
            f"   - Stage 3 worker: kubectl logs -n {namespace} -l app.kubernetes.io/component=stage3-worker --tail=100 -f"
        )
        print(f"   - All pods:       kubectl logs -n {namespace} --all-containers=true --tail=50 -f")
        print("\n🔧 Managing Deployment:")
        print(f"   - View pods:       kubectl get pods -n {namespace}")
        print(f"   - View HPAs:       kubectl get hpa -n {namespace}")
        print(f"   - View services:   kubectl get svc -n {namespace}")
        print(f"   - Watch pods:      kubectl get pods -n {namespace} --watch")
        print(f"   - Scale scrapy:    kubectl scale deployment/{release}-scrapy -n {namespace} --replicas=5")
        print(f"   - Describe pod:    kubectl describe pod -n {namespace} <pod-name>")
        print(f"   - Execute in pod:  kubectl exec -n {namespace} -it <pod-name> -- /bin/bash")
        print(f"{'=' * 70}\n")
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
        print(f"\nDeploying stage '{stage}' as Helm release '{release}' in namespace '{namespace}'...")
        deploy_helm_release(args.chart, release, namespace, values_files, set_args)
        wait_for_pods_ready(namespace, timeout=180)
        verify_hpa_status(namespace)
        print(f"\n{'=' * 70}")
        print(f"Stage '{stage}' Deployment Complete")
        print(f"{'=' * 70}")
        print(f"Release: {release}")
        print(f"Namespace: {namespace}")
        print("\n📝 Monitoring:")
        print(f"   - View pods:  kubectl get pods -n {namespace}")
        print(f"   - View logs:  kubectl logs -n {namespace} --all-containers=true --tail=100 -f")
        print(f"   - Watch:      kubectl get pods -n {namespace} --watch")
        print(f"{'=' * 70}\n")


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
