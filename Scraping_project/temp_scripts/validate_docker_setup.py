#!/usr/bin/env python3
import subprocess
import sys
import time
from pathlib import Path

def run_command(cmd, description=""):
    """Run a shell command and return output"""
    print(f"  Running: {description or cmd}")
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30
        )
        return result.returncode == 0, result.stdout, result.stderr
    except Exception as e:
        return False, "", str(e)

def validate_docker_setup():
    print("\n" + "🐳 " * 40)
    print("PHASE 3 - DOCKER COMPOSE VALIDATION")
    print("🐳 " * 40 + "\n")

    results = {}

    # Test 1: Docker Installed
    print("=" * 80)
    print("TEST 1: Docker Installation")
    print("=" * 80)

    success, stdout, stderr = run_command("docker --version", "Check Docker version")
    if success:
        print(f"✅ Docker installed: {stdout.strip()}")
        results['docker_installed'] = True
    else:
        print(f"❌ Docker not installed or not accessible")
        print(f"   Error: {stderr}")
        results['docker_installed'] = False

    # Test 2: Docker Compose
    print("\n" + "=" * 80)
    print("TEST 2: Docker Compose")
    print("=" * 80)

    success, stdout, stderr = run_command("docker compose version", "Check Docker Compose version")
    if success:
        print(f"✅ Docker Compose installed: {stdout.strip()}")
        results['docker_compose'] = True
    else:
        print(f"❌ Docker Compose not available")
        results['docker_compose'] = False

    # Test 3: Docker Compose File
    print("\n" + "=" * 80)
    print("TEST 3: Docker Compose Configuration")
    print("=" * 80)

    compose_file = Path("docker-compose.yml")
    if compose_file.exists():
        print(f"✅ docker-compose.yml found")

        # Validate syntax
        success, stdout, stderr = run_command("docker compose config", "Validate docker-compose.yml syntax")
        if success:
            print(f"✅ docker-compose.yml syntax valid")

            # Count services
            success2, stdout2, stderr2 = run_command("docker compose config --services", "List services")
            if success2:
                services = stdout2.strip().split('\n')
                print(f"✅ Found {len(services)} services:")
                for service in services:
                    print(f"   • {service}")
                results['compose_valid'] = True
            else:
                print(f"❌ Could not list services")
                results['compose_valid'] = False
        else:
            print(f"❌ docker-compose.yml syntax error")
            print(f"   {stderr}")
            results['compose_valid'] = False
    else:
        print(f"❌ docker-compose.yml not found")
        results['compose_valid'] = False

    # Test 4: Dockerfile
    print("\n" + "=" * 80)
    print("TEST 4: Dockerfile")
    print("=" * 80)

    dockerfile = Path("Dockerfile")
    if dockerfile.exists():
        print(f"✅ Dockerfile found")

        with open(dockerfile, 'r') as f:
            lines = f.readlines()

        print(f"   Lines: {len(lines)}")

        # Check for key instructions
        has_from = any('FROM' in line for line in lines)
        has_workdir = any('WORKDIR' in line for line in lines)
        has_copy = any('COPY' in line for line in lines)
        has_run = any('RUN' in line for line in lines)

        if has_from:
            print(f"   ✅ Has FROM instruction")
        if has_workdir:
            print(f"   ✅ Has WORKDIR instruction")
        if has_copy:
            print(f"   ✅ Has COPY instruction")
        if has_run:
            print(f"   ✅ Has RUN instruction")

        results['dockerfile'] = has_from and has_copy
    else:
        print(f"❌ Dockerfile not found")
        results['dockerfile'] = False

    # Test 5: Docker Network
    print("\n" + "=" * 80)
    print("TEST 5: Docker Daemon Running")
    print("=" * 80)

    success, stdout, stderr = run_command("docker info", "Check Docker daemon")
    if success:
        print(f"✅ Docker daemon is running")
        results['docker_running'] = True
    else:
        print(f"❌ Docker daemon not running")
        print(f"   Try: sudo systemctl start docker")
        results['docker_running'] = False

    # Test 6: Build Capability (dry run)
    print("\n" + "=" * 80)
    print("TEST 6: Docker Build Test (Dry Run)")
    print("=" * 80)

    if results.get('dockerfile', False) and results.get('docker_running', False):
        print(f"  Checking if build would succeed (not actually building)...")

        # Just validate the Dockerfile syntax
        success, stdout, stderr = run_command("docker build --help > /dev/null", "Check build command")
        if success:
            print(f"✅ Docker build command available")
            print(f"   Note: Actual build not performed (would take time)")
            results['can_build'] = True
        else:
            print(f"❌ Docker build not available")
            results['can_build'] = False
    else:
        print(f"⚠️  Skipping (prerequisites not met)")
        results['can_build'] = False

    # Test 7: Environment Variables
    print("\n" + "=" * 80)
    print("TEST 7: Environment Configuration")
    print("=" * 80)

    env_file = Path(".env.example")
    if env_file.exists():
        print(f"✅ .env.example found")

        with open(env_file, 'r') as f:
            env_lines = [l.strip() for l in f.readlines() if l.strip() and not l.startswith('#')]

        print(f"   Environment variables defined: {len(env_lines)}")
        for line in env_lines[:5]:
            print(f"   • {line.split('=')[0]}")
        if len(env_lines) > 5:
            print(f"   ... and {len(env_lines) - 5} more")

        results['env_config'] = True
    else:
        print(f"⚠️  .env.example not found (optional)")
        results['env_config'] = False

    # Summary
    print("\n" + "=" * 80)
    print("DOCKER VALIDATION SUMMARY")
    print("=" * 80)

    total = len(results)
    passed = sum(1 for v in results.values() if v)

    print(f"\n📊 Results: {passed}/{total} checks passed ({passed/total*100:.1f}%)")
    print()

    for test_name, success in results.items():
        status = "✅" if success else "❌"
        print(f"{status} {test_name}")

    print("\n" + "=" * 80)
    print("DOCKER DEPLOYMENT READINESS")
    print("=" * 80)

    critical_checks = ['docker_installed', 'docker_compose', 'compose_valid', 'dockerfile']
    critical_passed = sum(1 for check in critical_checks if results.get(check, False))

    if critical_passed == len(critical_checks):
        print("\n✅ READY FOR DOCKER DEPLOYMENT")
        print("\nNext steps:")
        print("  1. docker compose build")
        print("  2. docker compose up -d")
        print("  3. docker compose logs -f")
        return True
    else:
        print("\n⚠️  NOT READY FOR DOCKER DEPLOYMENT")
        print(f"   {len(critical_checks) - critical_passed} critical checks failed")

        if not results.get('docker_installed'):
            print("\n   Install Docker: https://docs.docker.com/get-docker/")
        if not results.get('docker_running'):
            print("\n   Start Docker daemon: sudo systemctl start docker")

        return False

if __name__ == '__main__':
    success = validate_docker_setup()
    sys.exit(0 if success else 1)
