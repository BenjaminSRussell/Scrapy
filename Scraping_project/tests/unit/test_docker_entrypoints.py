"""Import smoke for Docker/compose entrypoints (#142)."""

from __future__ import annotations


def test_src_main_importable():
    import src.main as main_mod

    assert callable(main_mod.main)


def test_stage_worker_shims_importable():
    import src.workers.stage1_worker as s1
    import src.workers.stage2_worker as s2
    import src.workers.stage3_worker as s3
    import src.workers.stage4_worker as s4

    assert callable(s1.main)
    assert callable(s2.main)
    assert callable(s3.main)
    assert callable(s4.main)
