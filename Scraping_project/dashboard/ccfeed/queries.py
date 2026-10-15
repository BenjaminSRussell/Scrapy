"""Pure builders for stage detail, URL journey and explore (shared by live and demo feeds)."""
from __future__ import annotations

from collections import Counter
from urllib.parse import urlsplit

from .stages import TS_COLUMN, event_fields, parse_ts, routed_to

EXPLORE_CHILDREN = 200
EXPLORE_URLS = 100
TOP_BARS = 8


# ---- stage detail ----

def _values(rows, key):
    return [r[key] for r in rows if r.get(key) is not None]


def _mean(values):
    return sum(values) / len(values) if values else None


def _share(values):
    return 100.0 * sum(1 for v in values if v) / len(values) if values else None


def _bars(bid, title, counter: Counter, order=None, limit=TOP_BARS):
    items = order(counter) if order else sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return {"id": bid, "title": title, "kind": "bars",
            "items": [{"label": str(k), "value": v} for k, v in items[:limit]]}


def _stats(items):
    present = [{"label": label, "value": value, "format": fmt} for label, value, fmt in items if value is not None]
    return {"id": "summary", "title": "Summary", "kind": "stats", "items": present} if present else None


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def build_stage_detail(stage_id: str, rows: list[dict], recent: list[dict]) -> dict:
    out = []
    if stage_id == "seeds":
        hosts = Counter(host_of(r["url"]) for r in rows if r.get("url"))
        if hosts:
            out.append(_bars("hosts", "Hosts", hosts))
    elif stage_id == "discovery":
        depths = Counter(_values(rows, "depth"))
        if depths:
            out.append(_bars("depth", "Depth", depths, order=lambda c: sorted(c.items()), limit=20))
        codes = Counter(_values(rows, "status_code"))
        if codes:
            out.append(_bars("status", "Status codes", codes))
        hosts = {r.get("domain") or host_of(r.get("url") or "") for r in rows} - {""}
        stats = _stats([("Needs JavaScript", _share(_values(rows, "requires_js")), "percent"),
                        ("Mean page size", _mean(_values(rows, "content_size")), "bytes"),
                        ("Hosts", len(hosts) if hosts else None, "int")])
        if stats:
            out.append(stats)
    elif stage_id == "analysis":
        routes = Counter(routed_to(r) for r in rows)
        if rows:
            labelled = [("Summaries", routes.get("summaries", 0)), ("Large Docs", routes.get("large_docs", 0)),
                        ("Not routed", routes.get(None, 0))]
            out.append({"id": "routing", "title": "Routing", "kind": "bars",
                        "items": [{"label": k, "value": v} for k, v in labelled if v]})
        stats = _stats([("Mean word count", _mean(_values(rows, "word_count")), "int"),
                        ("Quality content", _share(_values(rows, "has_quality_content")), "percent")])
        if stats:
            out.append(stats)
    elif stage_id == "summaries":
        models = Counter(_values(rows, "model"))
        if models:
            out.append(_bars("models", "Models", models))
    elif stage_id == "large_docs":
        stats = _stats([("Mean compression", _mean(_values(rows, "compression_ratio")), "ratio"),
                        ("Mean original words", _mean(_values(rows, "original_word_count")), "int")])
        if stats:
            out.append(stats)
    return {"breakdowns": out,
            "recent": [{"url": r.get("url") or "", "t": _row_time(r), "fields": event_fields(stage_id, r)}
                       for r in recent]}


def _row_time(row: dict) -> float | None:
    if row.get("_t") is not None:
        return row["_t"]
    return parse_ts(row.get(TS_COLUMN))


# ---- URL journey ----

_JOURNEY_FIELDS = {
    "seeds": (("Priority", ("priority", "seed_priority"), "int"),),
    "discovery": (("Depth", ("depth",), "int"), ("Status code", ("status_code",), "text"),
                  ("Page size", ("content_size",), "bytes"), ("Needs JavaScript", ("requires_js",), "bool"),
                  ("Links found", ("urls_found",), "int")),
    "analysis": (("Title", ("title",), "text"), ("Word count", ("word_count",), "int"),
                 ("Quality content", ("has_quality_content",), "bool"),
                 ("Large document", ("is_massive_doc",), "bool")),
    "summaries": (("Model", ("model",), "text"), ("Summary", ("summary",), "text")),
    "large_docs": (("Original words", ("original_word_count",), "int"),
                   ("Summary words", ("summary_word_count",), "int"),
                   ("Compression", ("compression_ratio",), "ratio"), ("Summary", ("summary",), "text")),
}
_STEP_TITLES = {"seeds": "Seeded", "discovery": "Discovered", "analysis": "Analyzed",
                "summaries": "Summarized", "large_docs": "Large Doc"}


def _fields(stage_id: str, row: dict) -> list[dict]:
    out = []
    for label, keys, fmt in _JOURNEY_FIELDS[stage_id]:
        value = next((row[k] for k in keys if row.get(k) is not None), None)
        if value is not None:
            out.append({"label": label, "value": value, "format": fmt})
    return out


def build_journey(url: str, found: dict[str, dict | None]) -> dict:
    parts = urlsplit(url)
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    analysis = found.get("analysis")
    route = routed_to(analysis) if analysis else None
    steps = []
    for stage_id in ("seeds", "discovery", "analysis", "summaries", "large_docs"):
        row = found.get(stage_id)
        status, note = ("done", None) if row else ("waiting", None)
        if not row:
            if stage_id == "seeds":
                status, note = "skipped", "Not a seed"
            elif stage_id == "summaries" and route == "large_docs":
                status, note = "skipped", "Routed to Large Docs"
            elif stage_id == "summaries" and route == "summaries" and analysis.get("has_quality_content") is False:
                status, note = "skipped", "Low-quality page"
            elif stage_id == "large_docs" and route == "summaries":
                status, note = "skipped", "Routed to Summaries"
        steps.append({"stage": stage_id, "title": _STEP_TITLES[stage_id], "status": status, "note": note,
                      "t": _row_time(row) if row else None, "fields": _fields(stage_id, row) if row else []})
    discovery = found.get("discovery") or {}
    return {"url": url, "host": (parts.hostname or "").lower(), "path": path,
            "parent_url": discovery.get("parent_url") or None, "steps": steps}


# ---- explore ----

class _Node:
    __slots__ = ("count", "children", "urls")

    def __init__(self):
        self.count = 0
        self.children: dict[str, _Node] = {}
        self.urls: list[tuple[str, int | None]] = []


class ExploreTree:
    """Host, then path segments. Counts are URLs at or under each node."""

    def __init__(self, urls_with_depth):
        self.root = _Node()
        self.depths: Counter = Counter()
        for url, depth in urls_with_depth:
            if not url:
                continue
            try:
                parts = urlsplit(url)
            except ValueError:
                continue
            host = (parts.hostname or "").lower()
            if not host:
                continue
            if depth is not None:
                self.depths[int(depth)] += 1
            node = self.root
            node.count += 1
            for seg in (host, *[s for s in parts.path.split("/") if s]):
                node = node.children.setdefault(seg, _Node())
                node.count += 1
            if len(node.urls) < EXPLORE_URLS:
                node.urls.append((url, depth))

    def node(self, path: list[str]) -> dict | None:
        node = self.root
        for seg in path:
            node = node.children.get(seg)
            if node is None:
                return None
        ranked = sorted(node.children.items(), key=lambda kv: (-kv[1].count, kv[0]))
        shown, rest = ranked[:EXPLORE_CHILDREN], ranked[EXPLORE_CHILDREN:]
        return {
            "path": list(path),
            "total": node.count,
            "children": [{"segment": seg, "count": child.count, "leaf": not child.children} for seg, child in shown],
            "other": sum(child.count for _, child in rest),
            "urls": [{"url": u, "depth": d} for u, d in node.urls],
            "depths": [{"label": str(k), "value": v} for k, v in sorted(self.depths.items())] if not path else None,
        }
