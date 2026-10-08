#!/usr/bin/env python3
"""
Pipeline Control Center - Dashboard Server

Serves the custom monitoring dashboard on port 8080.
This is completely separate from Grafana (port 3001).

Purpose:
- Real-time operational monitoring
- Pipeline stage visualization
- System health checks
- Activity logging

Grafana Purpose (Different):
- Historical analytics
- Custom queries
- Advanced visualizations
- Alert management
"""

import argparse
import base64
import hmac
import http.server
import json
import math
import os
import socketserver
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

# #735: loopback by default. Containers/LAN must opt in explicitly
# (CC_HOST=0.0.0.0 or --host 0.0.0.0), which prints a warning.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
PORT = DEFAULT_PORT  # kept for backwards compatibility
WILDCARD_HOSTS = frozenset({"", "0.0.0.0", "::", "[::]"})
DASHBOARD_DIR = Path(__file__).parent
# #721: only dashboard assets are served; source, tests, docs and dotfiles are 404.
ALLOWED_SUFFIXES = frozenset({".html", ".js", ".css", ".map", ".png", ".svg", ".ico", ".jpg", ".jpeg",
                              ".gif", ".webp", ".woff", ".woff2", ".json", ".txt"})
DENIED_DIRS = frozenset({"tests", "__pycache__", "node_modules"})

# #245: enforcing CSP. index.html has no inline <script> (feature flags live in
# features.js); inline style attributes remain, hence style-src 'unsafe-inline'.
# Chart.js is the only third-party script. connect-src is same-origin: metrics go
# through /api/metrics (#400). Extra origins (e.g. a cross-origin ?metrics= URL)
# must be allowed explicitly with CC_CONNECT_SRC.
CSP_BASE = {
    "default-src": "'self'",
    "script-src": "'self' https://cdn.jsdelivr.net",
    "style-src": "'self' 'unsafe-inline'",
    "img-src": "'self' data:",
    "font-src": "'self'",
    "connect-src": "'self'",
    "object-src": "'none'",
    "base-uri": "'none'",
    "form-action": "'none'",
    "frame-ancestors": "'none'",
}
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}

# #400: Prometheus-format metrics are fetched server-side, so the browser only
# talks to this origin.
DEFAULT_METRICS_UPSTREAM = "http://127.0.0.1:9090/metrics"
UPSTREAM_MAX_BYTES = 8 * 1024 * 1024
# #401: Redis keys whose depth is shown on the System tab. js_spider:priority_queue
# is the only Redis-backed work queue (stage 2-4 queues are Delta tables / Kafka).
DEFAULT_QUEUE_KEYS = ("js_spider:priority_queue",)
_LENGTH_OPS = {"list": "llen", "zset": "zcard", "set": "scard", "stream": "xlen", "hash": "hlen"}


def build_csp(env=None) -> str:
    """Enforcing CSP; CC_CONNECT_SRC adds space/comma separated http(s) origins."""
    env = os.environ if env is None else env
    directives = dict(CSP_BASE)
    extra = [o for o in env.get("CC_CONNECT_SRC", "").replace(",", " ").split()
             if o.startswith(("http://", "https://")) and not any(c in o for c in ";'\"")]
    if extra:
        directives["connect-src"] += " " + " ".join(extra)
    return "; ".join(f"{k} {v}" for k, v in directives.items())


def _env_float(env, name: str, default: float) -> float:
    try:
        value = float(env.get(name, default))
    except (TypeError, ValueError):
        raise SystemExit(f"invalid {name}: {env.get(name)!r}") from None
    if value < 0 or math.isnan(value):
        raise SystemExit(f"invalid {name}: {env.get(name)!r}")
    return value


class AuthGate:
    """Optional auth (#189/#455). Off unless CC_AUTH_TOKEN or CC_BASIC_AUTH is set.

    * ``CC_BASIC_AUTH=user:password``: browser login prompt (HTTP Basic).
    * ``CC_AUTH_TOKEN=<secret>``: ``Authorization: Bearer <secret>`` for scripts,
      or as the Basic password with any user name.
    Comparisons are constant-time. This is a shared-secret gate for LAN demos,
    not an identity provider: put real deployments behind an authenticating proxy.
    """

    def __init__(self, token: str = "", basic: str = ""):
        self.token = token or ""
        user, sep, password = (basic or "").partition(":")
        if basic and (not sep or not user or not password):
            raise SystemExit("CC_BASIC_AUTH must be user:password")
        self.basic = (user, password) if basic else None

    @classmethod
    def from_env(cls, env=None) -> "AuthGate":
        env = os.environ if env is None else env
        return cls(env.get("CC_AUTH_TOKEN", ""), env.get("CC_BASIC_AUTH", ""))

    @property
    def enabled(self) -> bool:
        return bool(self.token or self.basic)

    @staticmethod
    def _eq(a: str, b: str) -> bool:
        return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))

    def allows(self, header: str | None) -> bool:
        if not self.enabled:
            return True
        scheme, _, value = (header or "").partition(" ")
        scheme, value = scheme.lower(), value.strip()
        if scheme == "bearer" and self.token:
            return self._eq(value, self.token)
        if scheme == "basic":
            try:
                user, _, password = base64.b64decode(value, validate=True).decode("utf-8").partition(":")
            except (ValueError, UnicodeDecodeError):
                return False
            ok = False
            if self.basic:
                ok = self._eq(user, self.basic[0]) & self._eq(password, self.basic[1])
            if self.token:
                ok = ok | self._eq(password, self.token)
            return ok
        return False


class RateLimiter:
    """Per-client token bucket (#257): CC_RATE_LIMIT req/min, CC_RATE_BURST burst; 0 disables."""

    MAX_CLIENTS = 10_000

    def __init__(self, per_minute: float = 600, burst: float = 120, clock=time.monotonic):
        self.rate = per_minute / 60.0
        self.burst = max(1.0, burst)
        self.clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls, env=None) -> "RateLimiter":
        env = os.environ if env is None else env
        return cls(_env_float(env, "CC_RATE_LIMIT", 600), _env_float(env, "CC_RATE_BURST", 120))

    @property
    def enabled(self) -> bool:
        return self.rate > 0

    def check(self, client: str) -> tuple[bool, float, int]:
        """(allowed, retry_after_seconds, remaining_tokens)."""
        if not self.enabled:
            return True, 0.0, 0
        now = self.clock()
        with self._lock:
            tokens, last = self._buckets.get(client, (self.burst, now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            if tokens >= 1:
                tokens -= 1
                allowed, retry = True, 0.0
            else:
                allowed, retry = False, (1 - tokens) / self.rate
            if client not in self._buckets and len(self._buckets) >= self.MAX_CLIENTS:
                cutoff = now - self.burst / self.rate  # idle long enough to be full again
                self._buckets = {k: v for k, v in self._buckets.items() if v[1] > cutoff}
            self._buckets[client] = (tokens, now)
            return allowed, retry, int(tokens)


def parse_cors_origins(env=None) -> tuple[str, ...]:
    """CC_CORS_ORIGINS: comma list of exact origins, or '*' (local dev only). Default: none."""
    env = os.environ if env is None else env
    return tuple(o.strip().rstrip("/") for o in env.get("CC_CORS_ORIGINS", "").split(",") if o.strip())


def fetch_upstream(url: str, timeout: float = 4.0, opener=urllib.request.urlopen) -> bytes:
    """GET an http(s) URL with a size cap; raises on non-2xx or transport errors."""
    if urlsplit(url).scheme not in ("http", "https"):
        raise ValueError(f"METRICS_UPSTREAM must be http(s): {url!r}")
    req = urllib.request.Request(url, headers={"Accept": "text/plain"})
    with opener(req, timeout=timeout) as resp:  # nosec B310 - scheme checked above
        body = resp.read(UPSTREAM_MAX_BYTES + 1)
    if len(body) > UPSTREAM_MAX_BYTES:
        raise ValueError("upstream response too large")
    return body


def _redis_from_env(env=None):
    """Client from REDIS_URL, else REDIS_HOST/PORT/DB/PASSWORD (short timeouts)."""
    import redis  # lazy: the static dashboard works without the package

    env = os.environ if env is None else env
    opts = {"socket_timeout": 2, "socket_connect_timeout": 2, "decode_responses": True}
    if env.get("REDIS_URL"):
        return redis.Redis.from_url(env["REDIS_URL"], **opts)
    return redis.Redis(host=env.get("REDIS_HOST", "localhost"), port=int(env.get("REDIS_PORT", 6379)),
                       db=int(env.get("REDIS_DB", 0)), password=env.get("REDIS_PASSWORD") or None, **opts)


def queue_keys(env=None) -> tuple[str, ...]:
    env = os.environ if env is None else env
    raw = env.get("CC_QUEUE_KEYS")
    if raw is None:
        return DEFAULT_QUEUE_KEYS
    return tuple(k.strip() for k in raw.split(",") if k.strip())


def queue_depths(client, keys) -> list[dict]:
    """Type-aware depth per key (LLEN/ZCARD/SCARD/XLEN/HLEN); a missing key is 0."""
    out = []
    for key in keys:
        kind = client.type(key)
        kind = kind.decode() if isinstance(kind, bytes) else str(kind)
        op = _LENGTH_OPS.get(kind)
        depth = int(getattr(client, op)(key)) if op else 0
        out.append({"key": key, "type": kind, "depth": depth})
    return out


class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    """Custom request handler for dashboard files."""

    root: Path = DASHBOARD_DIR
    error_message_format = "<!DOCTYPE html><title>%(code)d</title><p>%(code)d %(message)s</p>\n"
    # Built once per process from the environment; tests swap these on a subclass.
    auth: AuthGate | None = None
    limiter: RateLimiter | None = None
    cors_origins: tuple[str, ...] | None = None
    csp: str | None = None
    metrics_upstream: str | None = None
    upstream_timeout: float = 4.0
    redis_factory = staticmethod(_redis_from_env)
    queue_keys: tuple[str, ...] | None = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(self.root), **kwargs)

    # ---- configuration (lazy so importing the module never reads env) ----
    @classmethod
    def configure(cls, env=None) -> None:
        env = os.environ if env is None else env
        cls.auth = AuthGate.from_env(env)
        cls.limiter = RateLimiter.from_env(env)
        cls.cors_origins = parse_cors_origins(env)
        cls.csp = build_csp(env)
        cls.metrics_upstream = env.get("METRICS_UPSTREAM", DEFAULT_METRICS_UPSTREAM)
        cls.upstream_timeout = _env_float(env, "CC_UPSTREAM_TIMEOUT", 4.0) or 4.0
        cls.queue_keys = queue_keys(env)

    def _cfg(self):
        if self.auth is None:
            type(self).configure()

    def list_directory(self, path):  # noqa: ARG002 - no directory listings (#721)
        self.send_error(404, "Not Found")
        return None

    def _is_servable(self, url_path: str) -> bool:
        parts = [p for p in url_path.split("?", 1)[0].split("#", 1)[0].split("/") if p]
        if not parts:
            return True
        if any(p.startswith(".") or p in DENIED_DIRS for p in parts):
            return False
        return Path(parts[-1]).suffix.lower() in ALLOWED_SUFFIXES

    def send_head(self):
        if not self._is_servable(self.path):
            self.send_error(404, "Not Found")
            return None
        return super().send_head()

    def end_headers(self):
        self._cfg()
        origin = (self.headers.get("Origin") or "").rstrip("/") if self.headers else ""
        if "*" in (self.cors_origins or ()):
            self.send_header("Access-Control-Allow-Origin", "*")
        elif origin and origin in (self.cors_origins or ()):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        if self.cors_origins:
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Content-Security-Policy", self.csp or build_csp({}))
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        super().end_headers()

    # ---- guards ----
    def _guard(self) -> bool:
        """Rate limit, then auth. False means a response was already sent."""
        self._cfg()
        assert self.limiter is not None and self.auth is not None
        allowed, retry, remaining = self.limiter.check(self.client_address[0])
        if not allowed:
            body = b"Too Many Requests\n"
            self.send_response(429)
            self.send_header("Retry-After", str(max(1, math.ceil(retry))))
            self.send_header("RateLimit-Remaining", "0")
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return False
        if not self.auth.allows(self.headers.get("Authorization")):
            body = b"Authentication required\n"
            self.send_response(401)
            if self.auth.basic or self.auth.token:
                self.send_header("WWW-Authenticate", 'Basic realm="Pipeline Control Center", charset="UTF-8"')
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return False
        return True

    def _send_bytes(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: dict) -> None:
        self._send_bytes(status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    # ---- API ----
    def _api_metrics(self) -> None:
        try:
            body = fetch_upstream(self.metrics_upstream or DEFAULT_METRICS_UPSTREAM, self.upstream_timeout)
        except (OSError, ValueError, urllib.error.URLError) as e:
            self._send_json(502, {"ok": False, "error": f"metrics upstream unavailable: {type(e).__name__}"})
            return
        self._send_bytes(200, body, "text/plain; version=0.0.4; charset=utf-8")

    def _api_health(self) -> None:
        try:
            fetch_upstream(self.metrics_upstream or DEFAULT_METRICS_UPSTREAM, self.upstream_timeout)
            metrics = {"ok": True}
        except (OSError, ValueError, urllib.error.URLError) as e:
            metrics = {"ok": False, "error": type(e).__name__}
        self._send_json(200, {"ok": True, "metrics_upstream": metrics})

    def _api_queues(self) -> None:
        keys = self.queue_keys if self.queue_keys is not None else DEFAULT_QUEUE_KEYS
        try:
            client = self.redis_factory()
            client.ping()
            queues = queue_depths(client, keys)
        except ImportError:
            self._send_json(503, {"ok": False, "error": "redis package not installed"})
            return
        except Exception as e:  # connection refused, auth, timeout: never report fake zeros
            self._send_json(503, {"ok": False, "error": f"redis unavailable: {type(e).__name__}"})
            return
        self._send_json(200, {"ok": True, "queues": queues, "checked_at": time.time()})

    API_ROUTES = {"/api/metrics": "_api_metrics", "/api/health": "_api_health", "/api/queues": "_api_queues"}

    def do_OPTIONS(self):
        self._cfg()
        if not self._guard_rate_only():
            return
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _guard_rate_only(self) -> bool:
        assert self.limiter is not None
        allowed, retry, _ = self.limiter.check(self.client_address[0])
        if allowed:
            return True
        self.send_response(429)
        self.send_header("Retry-After", str(max(1, math.ceil(retry))))
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def do_HEAD(self):
        if not self._guard():
            return
        return super().do_HEAD()

    def do_GET(self):
        if not self._guard():
            return
        route = urlsplit(self.path).path
        if route in self.API_ROUTES:
            getattr(self, self.API_ROUTES[route])()
            return
        if route == '/':
            self.path = '/index.html'
        if route == '/version.js':
            version = _cc_version()
            body = f"window.__CC_VERSION__ = {version!r};\n".encode()
            self._send_bytes(200, body, 'application/javascript; charset=utf-8')
            return
        return super().do_GET()


class DashboardServer(socketserver.ThreadingTCPServer):
    """Threaded so a slow /api/* upstream never blocks static assets."""

    daemon_threads = True
    allow_reuse_address = True


def _cc_version() -> str:
    """CC_VERSION env, else `git describe --always --dirty`, else 'dev' (#1041)."""
    env = os.environ.get('CC_VERSION')
    if env:
        return env
    try:
        out = subprocess.run(
            ['git', 'describe', '--always', '--dirty'],
            cwd=str(DASHBOARD_DIR), capture_output=True, text=True, timeout=2,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    return 'dev'

def resolve_bind(host: str | None = None, port: int | str | None = None, env=None) -> tuple[str, int]:
    """--host/--port, else CC_HOST/CC_PORT, else 127.0.0.1:8080 (#735)."""
    env = os.environ if env is None else env
    host = host if host is not None else env.get("CC_HOST", DEFAULT_HOST)
    raw_port = port if port is not None else env.get("CC_PORT", DEFAULT_PORT)
    try:
        port_num = int(raw_port)
    except (TypeError, ValueError):
        raise SystemExit(f"invalid port: {raw_port!r}") from None
    if not 0 <= port_num <= 65535:
        raise SystemExit(f"invalid port: {raw_port!r}")
    return str(host), port_num


def make_server(host: str, port: int, handler=DashboardHandler, server_factory=DashboardServer, env=None):
    """Create (but do not start) the server; the factory is injectable for tests."""
    env = os.environ if env is None else env
    if host in WILDCARD_HOSTS:
        print(f"⚠️  Binding the dashboard to all interfaces ({host or '*'}:{port}); "
              "keep it behind a trusted network.", file=sys.stderr)
        if not AuthGate.from_env(env).enabled:
            print("⚠️  No CC_AUTH_TOKEN / CC_BASIC_AUTH set: anyone who can reach this port can "
                  "view the dashboard. See dashboard/README.md (Exposing the dashboard).", file=sys.stderr)
    if "*" in parse_cors_origins(env):
        print("⚠️  CC_CORS_ORIGINS=* allows any web page to read this dashboard's API; "
              "use it for local development only.", file=sys.stderr)
    return server_factory((host, port), handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Serve the Pipeline Control Center")
    parser.add_argument("--host", default=None, help=f"bind address (default: CC_HOST or {DEFAULT_HOST})")
    parser.add_argument("--port", default=None, help=f"port (default: CC_PORT or {DEFAULT_PORT})")
    args = parser.parse_args(argv)
    host, port = resolve_bind(args.host, args.port)
    DashboardHandler.configure()
    shown = "localhost" if host in ("127.0.0.1", "localhost", "::1") else (host or "0.0.0.0")

    print("=" * 80)
    print("🎛️  PIPELINE CONTROL CENTER")
    print("=" * 80)
    print()
    print(f"📊 Dashboard:  http://{shown}:{port}")
    print(f"📈 Metrics:    /api/metrics -> {DashboardHandler.metrics_upstream}  (METRICS_UPSTREAM)")
    print(f"🔐 Auth:       {'on' if DashboardHandler.auth and DashboardHandler.auth.enabled else 'off (CC_AUTH_TOKEN / CC_BASIC_AUTH)'}")
    print("📉 Grafana:    http://localhost:3001 (separate analytics)")
    print()
    print("Purpose:")
    print("  - Real-time pipeline monitoring")
    print("  - Stage-by-stage visualization")
    print("  - System health indicators")
    print("  - Activity logging")
    print()
    print("Note: This dashboard is for OPERATIONS, Grafana is for ANALYTICS")
    print()
    print("Press Ctrl+C to stop the server")
    print("=" * 80)
    print()

    with make_server(host, port) as httpd:
        try:
            print(f"✅ Server started on {host or '*'}:{port}")
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n\n🛑 Shutting down dashboard server...")
            httpd.shutdown()

if __name__ == "__main__":
    main()
