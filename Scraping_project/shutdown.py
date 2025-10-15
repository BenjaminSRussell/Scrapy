#!/usr/bin/env python3
"""
Unified entry-point for shutting down the scraping pipeline resources.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from collections.abc import Iterable, Sequence

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

STATEFUL_SERVICE_VOLUMES = [
    "postgres_data",
]

DELTA_LAKE_VOLUMES = [
    "delta_data",
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
        help="Also remove Delta Lake volumes during local shutdown.",
    )
    parser.add_argument(
        "--skip-images",
        action="store_true",
        help="Skip Docker image removal (only stop containers and remove volumes).",
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


def list_docker_volumes() -> list[str]:
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

    def matching_names(base: str) -> list[str]:
        suffix = f"_{base}"
        return [name for name in existing if name == base or name.endswith(suffix)]

    removable = list(INFRA_VOLUMES)
    removable.extend(STATEFUL_SERVICE_VOLUMES)
    if purge_data:
        removable.extend(DELTA_LAKE_VOLUMES)

    for base in removable:
        matches = matching_names(base)
        for name in matches:
            print(f"Removing Docker volume '{name}'...")
            remove_volume(name)


def remove_leftover_containers() -> None:
    result = subprocess.run(
        ("docker", "ps", "-a", "--format", "{{.ID}} {{.Names}}"),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return

    prefixes = ("scraping_", "scraping-")
    for line in result.stdout.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) != 2:
            continue
        container_id, name = parts
        if not name.startswith(prefixes):
            continue
        print(f"Removing Docker container '{name}'...")
        result = subprocess.run(
            ("docker", "rm", "-f", container_id),
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 and "No such container" not in (result.stderr or ""):
            print(
                f"  Warning: unable to remove container '{name}': {result.stderr.strip()}",
                file=sys.stderr,
            )


def containers_using_image(image_ref: str) -> list[tuple[str, str]]:
    result = subprocess.run(
        (
            "docker",
            "ps",
            "-a",
            "--filter",
            f"ancestor={image_ref}",
            "--format",
            "{{.ID}} {{.Names}}",
        ),
        capture_output=True,
        text=True,
        check=False,
    )
    containers: list[tuple[str, str]] = []
    if result.returncode != 0 or not result.stdout.strip():
        return containers

    for line in result.stdout.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) != 2:
            continue
        containers.append((parts[0], parts[1]))
    return containers


def display_name_for_image(image_ref: str) -> str:
    result = subprocess.run(
        (
            "docker",
            "image",
            "inspect",
            image_ref,
            "--format",
            "{{if .RepoTags}}{{range $index, $tag := .RepoTags}}{{if $index}}, {{end}}{{$tag}}{{end}}{{end}}",
        ),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return image_ref


def remove_service_images() -> None:
    result = subprocess.run(
        ("docker-compose", "images", "--quiet"),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return

    image_ids = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    for image_id in image_ids:
        display = display_name_for_image(image_id)
        print(f"Removing Docker image '{display}'...")
        result = subprocess.run(
            ("docker", "image", "rm", image_id),
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            continue

        stderr = (result.stderr or "").strip()
        if "No such image" in stderr:
            continue

        if (
            "used by running container" in stderr
            or "is using its referenced image" in stderr
        ):
            containers = containers_using_image(image_id)
            if not containers:
                print(f"  Warning: image still in use: {stderr}", file=sys.stderr)
                continue

            prefixes = ("scraping_", "scraping-")
            removable = [item for item in containers if item[1].startswith(prefixes)]
            external = [item for item in containers if item not in removable]

            for container_id, name in removable:
                print(f"  Removing container '{name}' still using image '{display}'...")
                rm_result = subprocess.run(
                    ("docker", "rm", "-f", container_id),
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if rm_result.returncode != 0 and "No such container" not in (
                    rm_result.stderr or ""
                ):
                    print(
                        f"    Warning: unable to remove container '{name}': {rm_result.stderr.strip()}",
                        file=sys.stderr,
                    )

            if removable:
                retry = subprocess.run(
                    ("docker", "image", "rm", image_id),
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if retry.returncode == 0:
                    continue
                stderr = (retry.stderr or "").strip()

            if external:
                offenders = ", ".join(name for _, name in external)
                k8s_offenders = [
                    name for _, name in external if name.startswith("k8s_")
                ]
                if k8s_offenders:
                    print(
                        f"  Skipping removal of image '{display}' because Kubernetes-managed containers "
                        f"still depend on it: {', '.join(k8s_offenders)}.",
                        file=sys.stderr,
                    )
                    print(
                        "  Run 'python shutdown.py --env k8s' (or use Helm manually) to uninstall the cluster release, "
                        "then re-run the shutdown if you want these images removed.",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"  Skipping removal of image '{display}' because external containers "
                        f"still depend on it: {offenders}",
                        file=sys.stderr,
                    )
                continue

        print(
            f"  Warning: unable to remove image '{display}': {stderr}",
            file=sys.stderr,
        )


def detect_running_k8s_release() -> None:
    result = subprocess.run(
        ("docker", "ps", "--format", "{{.Names}} {{.Image}}"),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return

    offenders: list[str] = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) != 2:
            continue
        name, image = parts
        if not name.startswith("k8s_"):
            continue
        if "scraping-pipeline" in name or "scraping" in image:
            offenders.append(f"{name} ({image})")

    if offenders:
        print(
            "Detected Kubernetes-managed containers for the scraping pipeline still running:",
            file=sys.stderr,
        )
        for offender in offenders:
            print(f"  - {offender}", file=sys.stderr)
        print(
            "Use 'python shutdown.py --env k8s' to uninstall the Kubernetes release (or "
            "helm/kubectl manually) before rerunning the local shutdown to remove shared images.",
            file=sys.stderr,
        )


def shutdown_local(purge_data: bool, skip_images: bool) -> None:
    prompt_parts = [
        "This will stop all local containers and clear Docker volumes for caches,",
        "brokers, metrics, and Postgres.",
    ]
    if not skip_images:
        prompt_parts.append("Docker images will also be removed.")
    if purge_data:
        prompt_parts.append("Delta Lake storage will be removed (--purge-data).")
    else:
        prompt_parts.append(
            "Delta Lake storage is preserved (use --purge-data to remove)."
        )

    prompt_parts.append("\nType 'yes' to proceed: ")
    prompt = " ".join(prompt_parts)

    confirmation = input(prompt).strip().lower()
    if confirmation != "yes":
        print("Local shutdown aborted.")
        sys.exit(0)

    print("Stopping local environment with docker-compose down...")
    run_command(("docker-compose", "down", "--remove-orphans"))

    print("Removing any leftover project containers...")
    remove_leftover_containers()

    if not skip_images:
        print("Removing cached project images (this may take a moment)...")
        try:
            remove_service_images()
        except Exception as e:
            print(f"Warning: Some images could not be removed: {e}", file=sys.stderr)
            print(
                "You can manually remove images later with: docker image prune",
                file=sys.stderr,
            )
    else:
        print("Skipping Docker image removal (--skip-images flag was used).")

    detect_running_k8s_release()

    print("Cleaning up infrastructure volumes...")
    prune_local_volumes(purge_data)
    print("\n" + "=" * 70)
    print("Local Environment Shut Down Successfully!")
    print("=" * 70)
    if skip_images:
        print(
            "Note: Docker images were not removed. Run without --skip-images to remove them."
        )
    print("=" * 70 + "\n")


def build_k8s_targets(args: argparse.Namespace) -> Sequence[tuple[str, str, str]]:
    if args.stage == "pipeline":
        release = args.release or PIPELINE_RELEASE
        namespace = args.namespace or PIPELINE_NAMESPACE
        return [("pipeline", release, namespace)]

    stages = (
        ["stage1", "stage2", "stage3"] if args.stage == "all-stages" else [args.stage]
    )
    targets: list[tuple[str, str, str]] = []
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
            f"WARNING: This will uninstall Helm release '{release}' from namespace '{namespace}' (stage: {stage})."
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
        shutdown_local(args.purge_data, args.skip_images)
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
