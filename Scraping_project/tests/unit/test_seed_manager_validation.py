"""SeedManager: invalid seeds rejected, dedupe by URL and by hash (#255). Offline."""

from __future__ import annotations

import pytest

from src.lakehouse import SeedManager
from src.lakehouse.lakehouse_manager import InMemoryBackend
from src.lakehouse.seed_manager import default_url_hasher

pytestmark = pytest.mark.unit


class Recorder(InMemoryBackend):
    def __init__(self):
        super().__init__()
        self.merges: list[tuple[str, list[dict]]] = []

    def merge_into(self, table, rows, *a, **kw):
        self.merges.append((table, [dict(r) for r in rows]))
        return super().merge_into(table, rows, *a, **kw)


@pytest.mark.parametrize(
    "bad",
    ["", "   ", None, 42, "ftp://uconn.edu/f", "javascript:alert(1)", "mailto:x@uconn.edu", "uconn.edu/no-scheme",
     "https://", "https://uconn.edu/a b", "https://uconn.edu/" + "a" * 2100],
)
def test_invalid_seed_is_rejected_and_counted(bad):
    backend = Recorder()
    result = SeedManager(backend).add_urls_to_seeds([bad, "https://uconn.edu/ok"], "seed", "manual")
    assert result["rejected"] == 1
    assert result["seed_inserted"] == 1
    assert [r["url"] for r in backend.read("seed_urls")] == ["https://uconn.edu/ok"]


def test_all_invalid_writes_nothing():
    backend = Recorder()
    result = SeedManager(backend).add_urls_to_seeds(["ftp://x", ""], "seed", "manual", enqueue_stage2=True)
    assert result == {"seed_inserted": 0, "domain_inserted": 0, "uconn_inserted": 0, "stage2_enqueued": 0,
                      "rejected": 2}
    assert backend.merges == []  # not even the domain side table


def test_rejections_are_logged_with_stable_codes(caplog):
    SeedManager(Recorder()).add_urls_to_seeds(["ftp://x", "", "https://"], "seed", "manual")
    assert "'bad_scheme': 1" in caplog.text and "'empty': 1" in caplog.text and "'no_host': 1" in caplog.text


def test_surrounding_whitespace_is_trimmed_not_rejected():
    backend = Recorder()
    result = SeedManager(backend).add_urls_to_seeds(["  https://uconn.edu/a \n"], "seed", "manual")
    assert result["rejected"] == 0
    assert backend.read("seed_urls")[0]["url"] == "https://uconn.edu/a"


def test_dedupe_by_url_keeps_first_and_one_merge_row_per_hash():
    backend = Recorder()
    urls = ["https://uconn.edu/b", "https://uconn.edu/a", "https://uconn.edu/b"]
    SeedManager(backend).add_urls_to_seeds(urls, "seed", "manual")
    (rows,) = [r for t, r in backend.merges if t == "seed_urls"]
    assert [r["url"] for r in rows] == ["https://uconn.edu/b", "https://uconn.edu/a"]  # input order kept


def test_dedupe_by_hash_when_the_hasher_normalises():
    """Two spellings, one url_hash: a MERGE must never get two source rows for one key."""
    backend = Recorder()
    sm = SeedManager(backend, url_hasher=lambda u: default_url_hasher(u.lower().rstrip("/")))
    result = sm.add_urls_to_seeds(["https://UCONN.edu/A/", "https://uconn.edu/a"], "seed", "manual",
                                  enqueue_stage2=True)
    assert result["seed_inserted"] == 1 and result["stage2_enqueued"] == 1
    for _, rows in backend.merges:
        assert len({r["url_hash"] for r in rows}) == len(rows) == 1
    assert backend.read("seed_urls")[0]["url"] == "https://UCONN.edu/A/"  # first spelling wins


def test_rerun_is_idempotent():
    backend = Recorder()
    sm = SeedManager(backend)
    for _ in range(3):
        sm.add_urls_to_seeds(["https://uconn.edu/a", "https://uconn.edu/b"], "seed", "manual")
    assert len(backend.read("seed_urls")) == 2


def test_bulk_seed_sums_rejections_across_batches():
    backend = Recorder()
    urls = [f"https://uconn.edu/{i}" for i in range(5)] + ["ftp://bad", "", "https://uconn.edu/0"]
    total = SeedManager(backend).bulk_seed_from_list(urls, batch_size=3)
    assert total["rejected"] == 2
    assert len(backend.read("seed_urls")) == 5
    assert len([t for t, _ in backend.merges if t == "seed_urls"]) == 3
