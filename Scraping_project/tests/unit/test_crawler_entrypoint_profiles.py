"""#615: crawler-entrypoint.sh must not require Kafka in the core profile,
and must never wait forever for a dependency."""
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "docker" / "entrypoints" / "crawler-entrypoint.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("timeout") is None,
                                reason="needs bash and coreutils timeout")


@pytest.fixture
def listener():
    socks = []

    def make():
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen(16)
        socks.append(s)
        return s.getsockname()[1]

    yield make
    for s in socks:
        s.close()


def closed_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run(env_extra, timeout=30):
    env = {"PATH": os.pathsep.join([str(Path(sys.executable).parent), os.environ.get("PATH", "")]),
           "HOME": os.environ.get("HOME", "/tmp")}
    env.update(env_extra)
    t0 = time.monotonic()
    proc = subprocess.run(["bash", str(SCRIPT), "echo", "APP-STARTED"], env=env,
                          capture_output=True, text=True, timeout=timeout)
    return proc, time.monotonic() - t0


def test_core_profile_starts_without_kafka(listener):
    proc, _ = run({"REDIS_HOST": "127.0.0.1", "REDIS_PORT": str(listener())})
    assert proc.returncode == 0, proc.stderr
    assert "PROFILE: core" in proc.stdout
    assert "Kafka: skipped" in proc.stdout
    assert proc.stdout.rstrip().endswith("APP-STARTED")


def test_require_kafka_without_bootstrap_is_a_hard_error(listener):
    proc, _ = run({"REDIS_HOST": "127.0.0.1", "REDIS_PORT": str(listener()), "REQUIRE_KAFKA": "1"})
    assert proc.returncode != 0
    assert "REQUIRE_KAFKA=1" in proc.stderr
    assert "APP-STARTED" not in proc.stdout


def test_streaming_profile_waits_for_first_broker(listener):
    redis_port, kafka_port = listener(), listener()
    proc, _ = run({"REDIS_HOST": "127.0.0.1", "REDIS_PORT": str(redis_port),
                   "KAFKA_BOOTSTRAP_SERVERS": f"PLAINTEXT://127.0.0.1:{kafka_port},127.0.0.1:1"})
    assert proc.returncode == 0, proc.stderr
    assert "PROFILE: streaming" in proc.stdout and "Kafka is ready!" in proc.stdout
    assert proc.stdout.rstrip().endswith("APP-STARTED")


def test_unreachable_kafka_fails_within_the_bound_instead_of_looping(listener):
    proc, elapsed = run({"REDIS_HOST": "127.0.0.1", "REDIS_PORT": str(listener()),
                         "KAFKA_BOOTSTRAP_SERVERS": f"127.0.0.1:{closed_port()}",
                         "KAFKA_WAIT_TIMEOUT": "2"})
    assert proc.returncode == 1
    assert "not reachable after 2s" in proc.stderr
    assert "APP-STARTED" not in proc.stdout
    assert elapsed < 15


def test_unreachable_redis_fails_within_the_bound():
    proc, elapsed = run({"REDIS_HOST": "127.0.0.1", "REDIS_PORT": str(closed_port()),
                         "REDIS_WAIT_TIMEOUT": "2"})
    assert proc.returncode == 1
    assert "Redis" in proc.stderr and "not reachable" in proc.stderr
    assert elapsed < 15


def test_missing_redis_env_still_errors():
    proc, _ = run({})
    assert proc.returncode != 0 and "REDIS_HOST is not set" in proc.stderr
