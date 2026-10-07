"""Regression guards for bugs that were fixed in code but never pinned by a test.

#165  DeadLetterQueue.cleanup_old used timedelta without importing it.
#314  scout_spider.get_postgres_manager called an undefined get_postgres().
#367  src/stage2 and src/stage4 were implicit namespace packages.
#471  documented entrypoints had shebangs but no executable bit.
#571  run_multiple_scouts.py used `python` rather than `python3`.
#628  SPIDER_MODULES listed src.stage3, which holds a worker, not spiders.
(#313 is pinned by test_delta_helper_api_parity, #438 by test_docker_entrypoints.)
"""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timedelta
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[2]


def test_dlq_cleanup_old_removes_only_stale_entries(tmp_path):  # 165
    from src.utils.dead_letter_queue import DeadLetterQueue

    dlq = DeadLetterQueue(base_path=tmp_path)
    old = tmp_path / "old.json"
    new = tmp_path / "new.json"
    old.write_text(json.dumps({"timestamp": (datetime.now() - timedelta(days=40)).isoformat()}))
    new.write_text(json.dumps({"timestamp": datetime.now().isoformat()}))

    assert dlq.cleanup_old(days=30) == 1
    assert not old.exists()
    assert new.exists()


def test_scout_spider_has_no_undefined_postgres_stub():  # 314
    import src.stage1.scout_spider as scout

    source = Path(scout.__file__).read_text()
    assert "get_postgres()" not in source
    helper = getattr(scout, "get_postgres_manager", None)
    if helper is not None:
        # Whatever it is now, it must be the real factory, not a NameError stub.
        from src.utils.postgres import get_postgres_manager

        assert helper is get_postgres_manager


@pytest.mark.parametrize("pkg", ["stage2", "stage4"])
def test_stage_packages_are_regular_packages(pkg):  # 367
    assert (PROJECT / "src" / pkg / "__init__.py").is_file()


@pytest.mark.parametrize("name", ["cli.py", "start.py", "shutdown.py", "reseed.py", "drain_lake.py"])
def test_entrypoints_are_executable(name):  # 471
    path = PROJECT / name
    assert path.read_text().startswith("#!/usr/bin/env python3")
    if os.name == "posix":
        assert path.stat().st_mode & stat.S_IXUSR, f"{name} has a shebang but is not executable"


def test_run_multiple_scouts_uses_python3():  # 571
    first = (PROJECT / "run_multiple_scouts.py").read_text().splitlines()[0]
    assert first == "#!/usr/bin/env python3"


def test_spider_modules_default_lists_only_spider_packages():  # 628
    from src import settings

    assert "src.stage3" not in settings.SPIDER_MODULES
    assert settings.NEWSPIDER_MODULE in settings.SPIDER_MODULES
