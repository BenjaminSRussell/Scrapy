#!/usr/bin/env python3
"""
Unified entry-point for shutting down the scraping pipeline resources.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from typing import Iterable, List, Sequence, Tuple


REQUIRED_TOOLS = {
    "local": ("docker-compose",),
    "k8s": ("helm",),
}

INFRA_VOLUMES = [
    "redis_data",
    "kafka_data",
    "zookeeper_data",
    "zookeeper_logs",
    "prometheus_a_data",
    "prometheus_b_data",
    "alertmanager_1_data",
    "alertmanager_2_data",
    "alertmanager_3_data",
    "grafana_data",
]

PROJECT_DATA_VOLUMES = [
    "delta_data",
    "postgres_data",
]

PIPELINE_RELEASE = "scraping-pipeline"
PIPELINE_NAMESPACE = "scraping"
K8S_STAGE_DEFAULTS = {
    "stage1": {
        "release_suffix": "stage1",
        "namespace_suffix": "stage1",
    },
    "stage2": {
        "release_suffix": "stage2",
        "namespace_suffix": "stage2",
    },
    "stage3": {
        "release_suffix": "stage3",
        "namespace_suffix": "stage3",
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Tear down local or Kubernetes resources for the scraping pipeline.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--env",
        choices=("local", "k8s"),
        default="local",
        help="Target environment to stop. Use 'local' for docker-compose or 'k8s' for Helm on Kubernetes.",
    )
    parser.add_argument(
        "--stage",
        choices=("pipeline", "stage1", "stage2", "stage3", "all-stages"),
        default="pipeline",
        help="Matches the stage used at deployment time for Kubernetes clusters.",
    )
    parser.add_argument(
        "--release",
        help="Override the Helm release name when uninstalling a single stage.",
    )
    parser.add_argument(
        "--release-prefix",
        default="scraping-pipeline",
        help="Prefix used to derive release names when uninstalling multiple stages.",
    )
    parser.add_argument(
        "--namespace",
        help="Override the Kubernetes namespace when uninstalling a single stage.",
    )
    parser.add_argument(
        "--namespace-prefix",
        default="scraping",
        help="Prefix used to derive namespaces when uninstalling multiple stages.",
    )
    parser.add_argument(
        "--purge-data",
        action="store_true",
        help="Also remove project data volumes (Delta Lake, Postgres) during local shutdown.",
    )
    return parser.parse_args()


def ensure_tools_available(env: str) -> None:
    missing = [tool for tool in REQUIRED_TOOLS[env] if shutil.which(tool) is None]
    if not missing:
        return

    print(
        f"Missing required tooling for the '{env}' environment: {', '.join(missing)}.",
        file=sys.stderr,
    )
    print("Install the missing dependencies and retry.", file=sys.stderr)
    sys.exit(1)


def run_command(command: Iterable[str]) -> subprocess.CompletedProcess:
    cmd_list = list(command)
    try:
        return subprocess.run(cmd_list, check=True)
    except subprocess.CalledProcessError as exc:
        print(f"Command failed: {' '.join(cmd_list)}", file=sys.stderr)
        if exc.stdout:
            print(exc.stdout, file=sys.stderr)
        if exc.stderr:
            print(exc.stderr, file=sys.stderr)
        sys.exit(exc.returncode or 1)


def list_docker_volumes() -> List[str]:
    result = subprocess.run(
        ("docker", "volume", "ls", "--format", "{{.Name}}"),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def remove_volume(volume_name: str) -> None:
    subprocess.run(
        ("docker", "volume", "rm", volume_name),
        check=False,
        capture_output=True,
        text=True,
    )


def prune_local_volumes(purge_data: bool) -> None:
    existing = list_docker_volumes()
    if not existing:
        return

    def matching_names(base: str) -> List[str]:
        suffix = f"_{base}"
        return [
            name
            for name in existing
            if name == base or name.endswith(suffix)
        ]

    removable = list(INFRA_VOLUMES)
    if purge_data:
        removable.extend(PROJECT_DATA_VOLUMES)

    for base in removable:
        matches = matching_names(base)
        for name in matches:
            print(f"Removing Docker volume '{name}'...")
            remove_volume(name)


def shutdown_local(purge_data: bool) -> None:
    prompt_lines = [
        "This will stop all local containers and remove Docker volumes that store logs,",
        "metrics, and broker state. Delta Lake/Postgres data are retained unless",
        "you supply '--purge-data'.",
        "Type 'yes' to proceed: ",
    ]
    prompt = "\n".join(prompt_lines)
    confirmation = input(prompt).strip().lower()
    if confirmation != "yes":
        print("Local shutdown aborted.")
        sys.exit(0)

    print("Stopping local environment with docker-compose down...")
    run_command(("docker-compose", "down"))

    print("Cleaning up infrastructure volumes...")
    prune_local_volumes(purge_data)
    print("Local environment shut down successfully.")


def build_k8s_targets(args: argparse.Namespace) -> Sequence[Tuple[str, str, str]]:
    if args.stage == "pipeline":
        release = args.release or PIPELINE_RELEASE
        namespace = args.namespace or PIPELINE_NAMESPACE
        return [("pipeline", release, namespace)]

    stages = ["stage1", "stage2", "stage3"] if args.stage == "all-stages" else [args.stage]
    targets: List[Tuple[str, str, str]] = []
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
        targets.append((stage, release, namespace))
    return targets


def shutdown_k8s(args: argparse.Namespace) -> None:
    targets = build_k8s_targets(args)
    if args.stage == "all-stages" and (args.release or args.namespace):
        print(
            "Note: '--release' and '--namespace' overrides are ignored when uninstalling multiple stages.",
            file=sys.stderr,
        )

    for stage, release, namespace in targets:
        print(
            f"WARNING: This will uninstall Helm release '{release}' from namespace '{namespace}' "
            f"(stage: {stage})."
        )
        confirmation = input(f"Type the release name '{release}' to confirm: ").strip()
        if confirmation != release:
            print("Kubernetes shutdown aborted.")
            sys.exit(0)

        print(f"Uninstalling Helm release '{release}' in namespace '{namespace}'...")
        run_command(("helm", "uninstall", release, "--namespace", namespace))
        print(f"Helm release '{release}' has been uninstalled.")


def main() -> None:
    args = parse_args()
    ensure_tools_available(args.env)

    if args.env == "local":
        shutdown_local(args.purge_data)
    else:
        shutdown_k8s(args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:  # pylint: disable=broad-except
        print(f"Unexpected error: {exc}", file=sys.stderr)
        sys.exit(1)
