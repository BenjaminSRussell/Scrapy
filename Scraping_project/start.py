#!/usr/bin/env python3
"""
Unified entry-point for starting the scraping pipeline locally or on Kubernetes.

``--dry-run`` (#733) validates the configuration and prints the commands a real
run would execute. It runs no external command (docker, helm, kubectl), never
prompts, and opens no Redis or other service connection. It exits 1 and lists
every problem a real run would hit (missing tools, unreadable Compose file,
missing chart or values files, malformed --set).
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

from compose_cli import MISSING_HINT, compose_available, compose_cmd

REQUIRED_TOOLS = {
    "local": ("docker",),  # plus a Compose CLI: see compose_cli.py (#342)
    "k8s": ("kubectl", "helm"),
}

LOCAL_GRAFANA_URL = "http://localhost:3000"
LOCAL_PROMETHEUS_URLS: list[str] = [
    "http://localhost:9090",
]
LOCAL_SEED_FILE = Path("data/raw/uconn_urls.csv")
COMPOSE_FILE = Path(__file__).resolve().parent / "docker-compose.yml"

# Display labels for `docker-compose logs -f <service>` hints, in print order.
# Only services that the Compose file defines are printed (#399); names from
# the full Kafka stack (see #145) show up automatically once they exist.
LOCAL_SERVICE_LABELS: dict[str, str] = {
    "scraper": "Scraper (Stage 1)",
    "scrapy-app": "Scrapy app",
    "stage1-worker": "Stage 1 worker",
    "stage2-worker": "Stage 2 worker",
    "stage3-worker": "Stage 3 worker",
    "stage4-worker": "Stage 4 worker",
    "kafka": "Kafka",
    "redis": "Redis",
    "postgres": "PostgreSQL",
    "prometheus": "Prometheus",
    "grafana": "Grafana",
}
# Readiness gate for local mode: the first of these that the Compose file defines.
LOCAL_READINESS_CANDIDATES = ("postgres", "redis")
# Service used for one-off `run` commands (Delta reset).
LOCAL_APP_CANDIDATES = ("scraper", "scrapy-app")
# Stage 4 (PDF/OCR) is in the chart but off by default (#504): it needs the
# PDF/OCR image. --stage stage4 deploys it alone; --stage pipeline needs
# --set stage4Worker.enabled=true to include it.
K8S_STAGE4_NOTE = (
    "Stage 4 (PDF/OCR, stage4Worker) is off by default in the chart: use --stage stage4, "
    "or add --set stage4Worker.enabled=true to --stage pipeline."
)

DEFAULT_HELM_CHART = "k8s/helm/scraping-pipeline"
DEFAULT_HELM_VALUES = os.path.join(DEFAULT_HELM_CHART, "values.yaml")
PIPELINE_RELEASE = "scraping-pipeline"
PIPELINE_NAMESPACE = "scraping"
K8S_STAGE_DEFAULTS = {
    # Every stage sets all four workload toggles explicitly, so a stage release
    # never depends on chart defaults (#504).
    "stage1": {
        "release_suffix": "stage1",
        "namespace_suffix": "stage1",
        "set_overrides": (
            "stage2Worker.enabled=false",
            "stage3Worker.enabled=false",
            "stage4Worker.enabled=false",
        ),
    },
    "stage2": {
        "release_suffix": "stage2",
        "namespace_suffix": "stage2",
        "set_overrides": (
            "scrapyApp.enabled=false",
            "stage3Worker.enabled=false",
            "stage4Worker.enabled=false",
        ),
    },
    "stage3": {
        "release_suffix": "stage3",
        "namespace_suffix": "stage3",
        "set_overrides": (
            "scrapyApp.enabled=false",
            "stage2Worker.enabled=false",
            "stage4Worker.enabled=false",
        ),
    },
    "stage4": {
        "release_suffix": "stage4",
        "namespace_suffix": "stage4",
        "set_overrides": (
            "scrapyApp.enabled=false",
            "stage2Worker.enabled=false",
            "stage3Worker.enabled=false",
            "stage4Worker.enabled=true",
        ),
    },
}
K8S_ALL_STAGES = ("stage1", "stage2", "stage3", "stage4")


def compose_services(compose_file: Path | str = COMPOSE_FILE) -> list[str]:
    """Service names defined in the Compose file, in file order ([] if unreadable)."""
    try:
        import yaml  # PyYAML is in requirements.txt

        data = yaml.safe_load(Path(compose_file).read_text(encoding="utf-8")) or {}
    except Exception as exc:  # OSError, ImportError, yaml.YAMLError
        print(f"Warning: could not read {compose_file}: {exc}", file=sys.stderr)
        return []
    services = data.get("services") if isinstance(data, dict) else None
    return [str(name) for name in (services or {})]


def first_defined(candidates: Iterable[str], services: Iterable[str]) -> str | None:
    defined = set(services)
    return next((name for name in candidates if name in defined), None)


def local_log_hints(services: Iterable[str]) -> list[str]:
    """`<compose> logs -f` lines for defined services only, labelled where known."""
    defined = list(services)
    ordered = [name for name in LOCAL_SERVICE_LABELS if name in defined]
    ordered += [name for name in defined if name not in LOCAL_SERVICE_LABELS]
    dc = " ".join(compose())
    hints = [f"   - All services:        {dc} logs -f"]
    for name in ordered:
        label = f"{LOCAL_SERVICE_LABELS.get(name, name)}:"
        hints.append(f"   - {label:<20} {dc} logs -f {name}")
    return hints


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
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
        choices=("pipeline", "stage1", "stage2", "stage3", "stage4", "all-stages"),
        default="pipeline",
        help=(
            "Kubernetes only (--env k8s): which portion of the pipeline to deploy. "
            "Ignored by --env local, which always starts every Compose service. "
            + K8S_STAGE4_NOTE
        ),
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
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Validate configuration and print the planned commands without running anything: "
            "no docker/helm/kubectl, no prompts, no Redis connection. Exits 1 on problems (#733)."
        ),
    )
    return parser.parse_args(argv)


DRY_RUN_PREFIX = "[dry-run] would run:"
_dry_run = False


def set_dry_run(enabled: bool) -> None:
    global _dry_run
    _dry_run = bool(enabled)


def is_dry_run() -> bool:
    return _dry_run


def compose() -> tuple[str, ...]:
    """`docker-compose` or `docker compose` (#342). Dry runs never probe the plugin."""
    return compose_cmd(probe=not _dry_run)


def missing_tools(env: str) -> list[str]:
    missing = [tool for tool in REQUIRED_TOOLS[env] if shutil.which(tool) is None]
    if env == "local" and not compose_available(probe=not _dry_run):
        missing.append(MISSING_HINT)
    return missing


def preflight_problems(args: argparse.Namespace) -> list[str]:
    """Everything that would make a real run fail before it starts, without side effects."""
    problems: list[str] = []
    missing = missing_tools(args.env)
    if missing:
        problems.append(f"missing required tooling for '{args.env}': {', '.join(missing)}")
    if args.env == "local":
        if not compose_services():
            problems.append(f"Compose file {COMPOSE_FILE} is unreadable or defines no services")
        return problems
    if not os.path.isdir(args.chart):
        problems.append(f"Helm chart not found at: {args.chart}")
    for values_file in [args.values, *args.extra_values]:
        if values_file and not os.path.exists(values_file):
            problems.append(f"Helm values file not found: {values_file}")
    for item in args.set_overrides or []:
        key, sep, _ = (item or "").partition("=")
        if not sep or not key.strip():
            problems.append(f"--set expects KEY=VALUE, got {item!r}")
    return problems


def ensure_tools_available(env: str) -> None:
    missing = missing_tools(env)
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
    if _dry_run:
        print(f"{DRY_RUN_PREFIX} {' '.join(cmd_list)}")
        return subprocess.CompletedProcess(cmd_list, 0, "" if capture_output else None, "")
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
    if _dry_run:
        print(f"{DRY_RUN_PREFIX} {' '.join(compose())} exec -T {service} true  (poll up to {timeout}s)")
        return
    deadline = time.time() + timeout
    last_error = ""
    while time.time() < deadline:
        result = subprocess.run(
            (*compose(), "exec", "-T", service, "true"),
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
        print(f"Last {' '.join(compose())} exec error:\n{last_error}", file=sys.stderr)
    try:
        run_command((*compose(), "logs", "--tail", "50", service))
    finally:
        sys.exit(1)


def start_local(args: argparse.Namespace) -> None:
    services = compose_services()
    if getattr(args, "stage", "pipeline") != "pipeline":
        print(
            f"Note: --stage {args.stage} only applies to --env k8s; local mode starts every "
            f"Compose service. Use `{' '.join(compose())} up -d <service>` to start a subset.",
            file=sys.stderr,
        )
    print(f"Starting local environment with {' '.join(compose())}...")
    if services:
        print(f"Compose services: {', '.join(services)}")
    run_command((*compose(), "up", "-d"))

    ready_service = first_defined(LOCAL_READINESS_CANDIDATES, services) if services else "postgres"
    if ready_service:
        print(f"Waiting for '{ready_service}' service readiness (timeout={args.wait_timeout}s)...")
        wait_for_exec(ready_service, args.wait_timeout)
    else:
        print("No postgres/redis service defined; skipping readiness wait.")
    app_service = (first_defined(LOCAL_APP_CANDIDATES, services) if services else None) or "scraper"

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
                    *compose(),
                    "run",
                    "--rm",
                    "--no-deps",
                    "-T",
                    app_service,
                    "python",
                    "cli.py",
                    "reset",
                    "--force",
                )
            )
    else:
        print("Skipping Delta Lake reset. Use '--reset-delta' to wipe and reseed.")

    if _dry_run:
        return
    print("\n" + "=" * 70)
    print("Local Environment Started Successfully!")
    print("=" * 70)
    print(f"\n📊 Grafana Dashboard: {LOCAL_GRAFANA_URL}")
    print("   - Default credentials: admin / (password from .env GRAFANA_ADMIN_PASSWORD)")
    print("   - View real-time metrics, dashboards, and alerts")
    print("\n📈 Prometheus:")
    for url in LOCAL_PROMETHEUS_URLS:
        print(f"   - {url}")
    print("\n📝 Viewing Logs:")
    for line in local_log_hints(services):
        print(line)
    print("\n🔧 Other Useful Commands:")
    dc = " ".join(compose())
    print(f"   - Check service status: {dc} ps")
    print(f"   - Stop all services:    {dc} down")
    print(f"   - Restart a service:    {dc} restart <service-name>")
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
    if _dry_run:
        print("[dry-run] would ask for 'yes' to confirm the checklist; not prompting.")
        return
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
    if not _dry_run:
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
    if _dry_run:
        print(f"{DRY_RUN_PREFIX} kubectl get pods --namespace {namespace}  (poll up to {timeout}s)")
        return
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
    if _dry_run:
        print(f"{DRY_RUN_PREFIX} kubectl get hpa --namespace {namespace}")
        return
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
    print(f"Note: {K8S_STAGE4_NOTE}")
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
        if not _dry_run:
            print("\nDeployment complete! Waiting for pods to be ready...")
        wait_for_pods_ready(namespace, timeout=300)
        verify_hpa_status(namespace)
        if _dry_run:
            return
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

    stages = list(K8S_ALL_STAGES) if args.stage == "all-stages" else [args.stage]
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
        if _dry_run:
            continue
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


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.dry_run:
        return dry_run(args)
    ensure_tools_available(args.env)

    if args.env == "local":
        start_local(args)
    else:
        start_k8s(args)
    return 0


def dry_run(args: argparse.Namespace) -> int:
    """Validate and print the plan (#733). Returns the exit code; never executes anything."""
    print(f"[dry-run] start.py --env {args.env}: validating configuration; nothing will be executed.")
    problems = preflight_problems(args)
    set_dry_run(True)
    try:
        if args.env == "local":
            start_local(args)
        else:
            start_k8s(args)
    finally:
        set_dry_run(False)
    if problems:
        print("\n[dry-run] configuration problems (a real run would fail):", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print("\n[dry-run] configuration OK; the commands above are what a real run would execute.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:  # pylint: disable=broad-except
        print(f"Unexpected error: {exc}", file=sys.stderr)
        sys.exit(1)
