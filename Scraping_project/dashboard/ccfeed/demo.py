"""DemoFeed: a seeded simulation of a crawl over UConn-style hosts.

Same interface as LiveFeed. It simulates in 1 s steps up to the wall clock,
after a 30-minute warm-up so charts start full. A separate RNG drives
service jitter, so the crawl itself is repeatable for a given seed.
"""
from __future__ import annotations

import bisect
import math
import random
import threading
import time
from collections import deque
from urllib.parse import urlsplit

from .queries import ExploreTree, build_journey, build_stage_detail
from .stages import STAGE_BY_ID, STAGES, PollResult, StageState, make_error_event, make_event

DEMO_HOSTS = (
    "uconn.edu", "today.uconn.edu", "admissions.uconn.edu", "library.uconn.edu", "health.uconn.edu",
    "research.uconn.edu", "engineering.uconn.edu", "catalog.uconn.edu", "events.uconn.edu",
    "career.uconn.edu", "law.uconn.edu", "nursing.uconn.edu",
)
SECTIONS = (
    "about", "news", "events", "programs", "undergraduate", "graduate", "research", "faculty", "staff",
    "students", "resources", "calendar", "directory", "departments", "apply", "visit", "tuition",
    "financial-aid", "housing", "dining", "athletics", "alumni", "giving", "policies", "archives", "labs",
    "centers", "courses", "majors", "people", "publications", "seminars", "stories", "guides", "services",
)
ADJECTIVES = ("annual", "spring", "fall", "summer", "winter", "new", "graduate", "undergraduate", "faculty",
              "student", "community", "research", "campus", "global", "digital", "first-year", "honors")
NOUNS = ("open-house", "orientation", "symposium", "report", "guide", "handbook", "award", "fellowship",
         "seminar", "workshop", "lecture", "update", "profile", "project", "initiative", "program", "grant",
         "series", "showcase", "forum", "policy", "checklist", "deadlines", "overview")
ERROR_TYPES = ("timeout", "http_404", "connection_reset", "http_503", "dns_failure")
MODELS = ("facebook/bart-large-cnn", "google/pegasus-large")

WARM_UP = 1800.0
MAX_CATCH_UP = 3600.0
MAX_URLS = 60_000
EVICT = 10_000
MAX_BACKLOG = 5_000  # per stage queue; older work is dropped, as a real backlog would be shed
REPLAY_EVENTS = 50
EVENTS_PER_POLL = 500
SAMPLES_KEPT = 1900
QUEUE_KEY = "js_spider:priority_queue"


class DemoFeed:
    mode = "demo"

    def __init__(self, seed: int = 7, clock=time.time, max_urls: int = MAX_URLS, evict: int = EVICT,
                 max_backlog: int = MAX_BACKLOG):
        self.seed = seed
        self.clock = clock
        self.max_urls = max_urls
        self.evict_count = evict
        self.max_backlog = max_backlog
        self.rng = random.Random(seed)
        self._svc_rng = random.Random(seed + 1)
        self._sim_t: float | None = None
        self._rows: dict[str, list[dict]] = {s.id: [] for s in STAGES}
        self._by_url: dict[str, dict[str, dict]] = {}
        self._queues = {k: deque() for k in ("analysis", "summaries", "large_docs")}
        self._counts = {s.id: 0 for s in STAGES}
        self._writes = {s.id: 0 for s in STAGES}
        self._last_write: dict[str, float | None] = {s.id: None for s in STAGES}
        self._samples = {s.id: deque(maxlen=SAMPLES_KEPT) for s in STAGES}
        self._acc = {s.id: 0.0 for s in STAGES}
        self._errors = 0
        self._pending: list[dict] = []
        self._seq = 0
        self._replayed = False
        self._explore: tuple[int, float, ExploreTree] | None = None
        self._lock = threading.Lock()

    # ---- simulation ----
    def _ensure(self, now: float) -> None:
        if self._sim_t is None:
            self._sim_t = now - WARM_UP
            self._plant_seeds(self._sim_t)
        if now - self._sim_t > MAX_CATCH_UP:  # e.g. after a laptop sleep: skip the gap
            self._sim_t = now - MAX_CATCH_UP
        while self._sim_t < now:
            dt = min(1.0, now - self._sim_t)
            self._sim_t += dt
            self._step(self._sim_t, dt)

    def _plant_seeds(self, t: float) -> None:
        roots = [f"https://{h}/" for h in DEMO_HOSTS]
        roots += [f"https://uconn.edu/{s}/" for s in ("academics", "admissions", "research", "news")]
        for url in roots:
            self._add_seed(url, t)
            self._add_discovery(url, None, 0, t)

    def _arrivals(self, stage_id: str, per_min: float, dt: float) -> int:
        self._acc[stage_id] += per_min / 60.0 * dt * self.rng.uniform(0.4, 1.6)
        n = int(self._acc[stage_id])
        self._acc[stage_id] -= n
        return n

    def _step(self, t: float, dt: float) -> None:
        wave = math.sin(2 * math.pi * t / 900) * 28 + math.sin(2 * math.pi * t / 173) * 9
        for _ in range(self._arrivals("discovery", 80 + wave, dt)):
            self._discover(t)
        for _ in range(self._arrivals("analysis", 30 + wave / 6, dt)):
            if self._queues["analysis"]:
                self._analyze(self._queues["analysis"].popleft(), t)
        for _ in range(self._arrivals("summaries", 24, dt)):
            if self._queues["summaries"]:
                self._summarize(self._queues["summaries"].popleft(), t)
        for _ in range(self._arrivals("large_docs", 1.8, dt)):
            if self._queues["large_docs"]:
                self._large_doc(self._queues["large_docs"].popleft(), t)
        if self._arrivals("seeds", 0.25, dt):
            host = self.rng.choice(DEMO_HOSTS)
            url = f"https://{host}/{self.rng.choice(SECTIONS)}/"
            if url not in self._by_url:
                self._add_seed(url, t)
        for sid in self._samples:
            self._samples[sid].append((t, self._counts[sid]))
        if len(self._rows["discovery"]) > self.max_urls:
            self._evict()

    # ---- writers ----
    def _write(self, stage_id: str, row: dict, t: float, emit: bool = True) -> None:
        row["_t"] = t
        self._rows[stage_id].append(row)
        self._by_url.setdefault(row["url"], {})[stage_id] = row
        self._counts[stage_id] += 1
        self._writes[stage_id] += 1
        self._last_write[stage_id] = t
        if emit:
            self._seq += 1
            self._pending.append(make_event(STAGE_BY_ID[stage_id], row, self._seq, t))

    def _add_seed(self, url: str, t: float) -> None:
        host = urlsplit(url).hostname
        self._write("seeds", {"url": url, "domain": host, "priority": self.rng.randint(0, 3)}, t)

    def _add_discovery(self, url: str, parent: str | None, depth: int, t: float) -> None:
        r = self.rng.random()
        status = 200 if r < 0.93 else (301 if r < 0.97 else 404)
        row = {"url": url, "domain": urlsplit(url).hostname, "depth": depth, "status_code": status,
               "content_size": int(self.rng.lognormvariate(10.4, 0.7)),
               "requires_js": self.rng.random() < 0.08, "is_heavy": self.rng.random() < 0.05,
               "urls_found": self.rng.randint(0, 90), "parent_url": parent}
        self._write("discovery", row, t)
        if status == 200:
            self._enqueue("analysis", url)

    def _discover(self, t: float) -> None:
        parent = self._pick_parent()
        if parent is None:
            return
        if self.rng.random() < 0.02:
            self._errors += 1
            self._seq += 1
            self._pending.append(make_error_event(
                {"url": self._child_url(parent), "error_type": self.rng.choice(ERROR_TYPES)}, self._seq, t))
            return
        self._add_discovery(self._child_url(parent), parent["url"], parent["depth"] + 1, t)

    def _pick_parent(self) -> dict | None:
        rows = self._rows["discovery"]
        for _ in range(8):
            pool = rows[-400:] if self.rng.random() < 0.85 else rows[:60]
            cand = self.rng.choice(pool)
            if cand["depth"] < 6 and not cand["url"].endswith(".pdf") and cand["status_code"] == 200:
                return cand
        return rows[0] if rows else None

    def _child_url(self, parent: dict) -> str:
        parts = urlsplit(parent["url"])
        base = parts.path.rstrip("/")
        if parent["depth"] == 0:
            seg = self.rng.choice(SECTIONS)
        else:
            seg = f"{self.rng.choice(ADJECTIVES)}-{self.rng.choice(NOUNS)}"
            if self.rng.random() < 0.3:
                seg += f"-{self.rng.randint(2019, 2026)}"
            if self.rng.random() < 0.06:
                seg += ".pdf"
        url = f"https://{parts.hostname}{base}/{seg}"
        n = 2
        while url in self._by_url:
            url = f"https://{parts.hostname}{base}/{seg}-{n}"
            n += 1
        return url

    def _enqueue(self, stage_id: str, url: str) -> None:
        queue = self._queues[stage_id]
        queue.append(url)
        while len(queue) > self.max_backlog:
            queue.popleft()  # shed the oldest work rather than grow without bound

    def _analyze(self, url: str, t: float) -> None:
        if url not in self._by_url:
            return
        pdf = url.endswith(".pdf")
        massive = self.rng.random() < (0.85 if pdf else 0.01)
        words = self.rng.randint(9_000, 80_000) if massive else int(self.rng.lognormvariate(6.4, 0.6))
        quality = massive or self.rng.random() < 0.75
        row = {"url": url, "domain": urlsplit(url).hostname, "title": _title(url), "word_count": words,
               "has_quality_content": quality, "is_massive_doc": massive,
               "quality_score": round(self.rng.uniform(0.55, 0.98) if quality else self.rng.uniform(0.1, 0.5), 3)}
        self._write("analysis", row, t)
        if massive:
            self._enqueue("large_docs", url)
        elif quality:
            self._enqueue("summaries", url)

    def _summarize(self, url: str, t: float) -> None:
        if url not in self._by_url:
            return
        title = _title(url)
        model = MODELS[0] if self.rng.random() < 0.85 else MODELS[1]
        self._write("summaries", {"url": url, "title": title, "model": model, "summary": _summary(title, url)}, t)

    def _large_doc(self, url: str, t: float) -> None:
        analysis = self._by_url.get(url, {}).get("analysis")
        if analysis is None:
            return
        original = analysis["word_count"]
        short = self.rng.randint(350, 900)
        self._write("large_docs", {"url": url, "title": _title(url), "original_word_count": original,
                                   "summary_word_count": short, "compression_ratio": round(short / original, 4),
                                   "summary": _summary(_title(url), url)}, t)

    def _evict(self) -> None:
        gone = {r["url"] for r in self._rows["discovery"][: self.evict_count]}
        for sid in self._rows:
            self._rows[sid] = [r for r in self._rows[sid] if r["url"] not in gone]
        for url in gone:
            self._by_url.pop(url, None)
        for sid, queue in self._queues.items():
            self._queues[sid] = deque(u for u in queue if u not in gone)

    # ---- Feed interface ----
    def poll(self, now: float) -> PollResult:
        with self._lock:
            self._ensure(now)
            stages = {s.id: StageState(status="ok", count=self._counts[s.id], last_write=self._last_write[s.id],
                                       version=self._writes[s.id]) for s in STAGES}
            stages["discovery"].errors = self._errors
            if self._replayed:
                events = self._pending[-EVENTS_PER_POLL:]
            else:
                self._replayed = True
                events = [{**e, "replay": True} for e in self._pending[-REPLAY_EVENTS:]]
            self._pending = []
            return PollResult(stages=stages, events=events)

    def backfill(self, now: float, seconds: int, bucket: int) -> dict[str, list[tuple[float, int]]]:
        with self._lock:
            self._ensure(now)
            start = now - seconds
            ends = [start + bucket * (i + 1) for i in range(seconds // bucket)]
            out = {}
            for sid, samples in self._samples.items():
                times = [t for t, _ in samples]
                points = []
                for end in ends:
                    i = bisect.bisect_right(times, end) - 1
                    points.append((end, samples[i][1] if i >= 0 else 0))
                out[sid] = points
            return out

    def services(self, now: float) -> list[dict]:
        with self._lock:
            depth = len(self._queues["analysis"])
            newest = max((t for t in self._last_write.values() if t), default=None)
        latency = round(self._svc_rng.uniform(0.6, 2.4), 2)
        return [
            {"id": "redis", "label": "Redis", "status": "ok", "detail": f"Simulated · {latency} ms · 1 queue",
             "checked_at": now, "data": {"latency_ms": latency,
                                         "queues": [{"key": QUEUE_KEY, "type": "zset", "depth": depth}]}},
            {"id": "delta", "label": "Delta Lake", "status": "ok", "detail": "Simulated · 5/5 tables",
             "checked_at": now, "data": {"path": "(simulated)", "tables_found": 5, "tables_total": 5,
                                         "last_write": newest}},
            {"id": "metrics", "label": "Metrics upstream", "status": "ok", "detail": "Simulated",
             "checked_at": now, "data": {}},
        ]

    def stage_detail(self, stage_id: str) -> dict | None:
        if stage_id not in STAGE_BY_ID:
            return None
        with self._lock:
            rows = list(self._rows[stage_id])
        return build_stage_detail(stage_id, rows, list(reversed(rows[-50:])))

    def url_journey(self, url: str) -> dict | None:
        with self._lock:
            record = self._by_url.get(url)
            if not record:
                return None
            found = {s.id: record.get(s.id) for s in STAGES}
        return build_journey(url, found)

    def explore(self, path: list[str]) -> dict | None:
        with self._lock:
            now = self._sim_t or 0.0
            version = self._writes["discovery"]
            if self._explore is None or (self._explore[0] != version and now - self._explore[1] >= 15):
                tree = ExploreTree((r["url"], r["depth"]) for r in self._rows["discovery"])
                self._explore = (version, now, tree)
            tree = self._explore[2]
        return tree.node(path)


def _title(url: str) -> str:
    seg = [s for s in urlsplit(url).path.split("/") if s]
    word = (seg[-1] if seg else urlsplit(url).hostname.split(".")[0]).removesuffix(".pdf")
    return " ".join(w.capitalize() for w in word.replace("_", "-").split("-") if w) or "Home"


def _summary(title: str, url: str) -> str:
    host = urlsplit(url).hostname
    return (f"{title} from {host}: key dates, who it is for, and where to start. "
            f"The page links to related resources and contacts for follow-up questions.")
