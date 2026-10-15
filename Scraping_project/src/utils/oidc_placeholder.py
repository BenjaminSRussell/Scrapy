"""Disabled OIDC SSO placeholder (#673).

Operators can save issuer, client, redirect, and scope fields. Nothing here
contacts an identity provider. ``oidc_login`` always returns
``not_implemented``, and ``enabled`` is stored and reported as false.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

NOT_IMPLEMENTED = "not_implemented"
CONFIG_PATH = "/api/sso/oidc"
LOGIN_PATH = "/api/sso/oidc/login"

DEFAULT_SCOPES = "openid profile email"
_URL_MAX = 512
_ID_MAX = 256
_SECRET_MAX = 512
_SCOPES_MAX = 256
_STORE_MAX_BYTES = 16_384
_SCOPE_TOKEN = re.compile(r"^[A-Za-z0-9._:-]+$")
_ALLOWED_KEYS = frozenset({"issuer", "client_id", "client_secret", "redirect_uri", "scopes", "enabled"})
_TEXT_FIELDS = ("issuer", "client_id", "redirect_uri", "scopes")

_SHIPPED_CONFIG = Path(__file__).resolve().parents[2] / "config.yml"
_DEFAULT_OVERLAY = Path(__file__).resolve().parents[2] / "dashboard" / ".oidc-sso.json"


class OidcConfigError(ValueError):
    """A saved OIDC field was rejected. ``field`` is the object key, not a secret."""

    def __init__(self, field: str, message: str) -> None:
        self.field = field
        super().__init__(f"{field}: {message}")


def oidc_login(_config: Mapping[str, Any] | None = None) -> str:
    """SSO login path. Always ``not_implemented``.

    Saved issuer and client fields are ignored. This does not open a socket
    and does not issue a redirect. A later enablement has to replace this
    function; flipping ``enabled`` is not enough.
    """
    return NOT_IMPLEMENTED


def _empty_doc() -> dict[str, Any]:
    return {
        "issuer": "",
        "client_id": "",
        "client_secret": "",
        "redirect_uri": "",
        "scopes": DEFAULT_SCOPES,
        "enabled": False,
    }


def _shipped_strings(path: Path | None) -> dict[str, str]:
    """String fields from config.yml ``oidc:``. Missing or unreadable means none.

    ``enabled`` is not copied. A true value in YAML does not turn SSO on.
    ``client_secret`` is never read from config.yml.
    """
    config_path = _SHIPPED_CONFIG if path is None else path
    if not config_path.is_file():
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    try:
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(loaded, dict):
        return {}
    section = loaded.get("oidc")
    if not isinstance(section, dict):
        return {}
    out: dict[str, str] = {}
    for key in _TEXT_FIELDS:
        value = section.get(key)
        if isinstance(value, str):
            out[key] = value
    return out


def _require_str(payload: Mapping[str, Any], field: str) -> str:
    if field not in payload:
        raise OidcConfigError(field, "required")
    value = payload[field]
    if not isinstance(value, str):
        raise OidcConfigError(field, "must be a string")
    return value


def _no_markup(value: str, field: str) -> None:
    if any(ch in value for ch in "<>\"'") or any(ord(ch) < 32 for ch in value):
        raise OidcConfigError(field, "must not contain quotes, brackets, or control characters")


def _http_url(value: str, field: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if len(value) > _URL_MAX:
        raise OidcConfigError(field, f"must be at most {_URL_MAX} characters")
    if any(ch.isspace() for ch in value):
        raise OidcConfigError(field, "must not contain whitespace")
    _no_markup(value, field)
    parsed = urlsplit(value)
    if parsed.scheme not in ("https", "http") or not parsed.netloc:
        raise OidcConfigError(field, "must be an http(s) URL")
    if parsed.username or parsed.password:
        raise OidcConfigError(field, "must not include userinfo")
    if parsed.fragment:
        raise OidcConfigError(field, "must not include a fragment")
    return value


def _client_id(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if len(value) > _ID_MAX:
        raise OidcConfigError("client_id", f"must be at most {_ID_MAX} characters")
    if any(ch.isspace() for ch in value):
        raise OidcConfigError("client_id", "must not contain whitespace")
    _no_markup(value, "client_id")
    return value


def _secret(value: object) -> str:
    if not isinstance(value, str):
        raise OidcConfigError("client_secret", "must be a string")
    if len(value) > _SECRET_MAX:
        raise OidcConfigError("client_secret", f"must be at most {_SECRET_MAX} characters")
    if any(ord(ch) < 32 for ch in value):
        raise OidcConfigError("client_secret", "must not contain control characters")
    return value


def _scopes(value: str) -> str:
    collapsed = " ".join(value.split())
    if len(collapsed) > _SCOPES_MAX:
        raise OidcConfigError("scopes", f"must be at most {_SCOPES_MAX} characters")
    if not collapsed:
        return ""
    for token in collapsed.split(" "):
        if not _SCOPE_TOKEN.match(token):
            raise OidcConfigError("scopes", "tokens must be letters, digits, or . _ : -")
    return collapsed


def _check_shape(payload: Mapping[str, Any]) -> None:
    extra = [key for key in payload if key not in _ALLOWED_KEYS]
    if extra:
        raise OidcConfigError(str(sorted(extra)[0]), "unknown field")
    if "enabled" in payload and not isinstance(payload["enabled"], bool):
        raise OidcConfigError("enabled", "must be a boolean; SSO stays disabled either way")


def _public(doc: Mapping[str, Any]) -> dict[str, Any]:
    secret = doc.get("client_secret")
    return {
        "ok": True,
        "issuer": str(doc.get("issuer") or ""),
        "client_id": str(doc.get("client_id") or ""),
        "redirect_uri": str(doc.get("redirect_uri") or ""),
        "scopes": str(doc.get("scopes") or ""),
        "enabled": False,
        "client_secret_set": bool(isinstance(secret, str) and secret),
        "login": NOT_IMPLEMENTED,
    }


class OidcConfigStore:
    """In-memory view of the OIDC placeholder, optionally backed by a 0600 JSON file.

    ``CC_OIDC_CONFIG_PATH`` selects the file. Unset uses
    ``dashboard/.oidc-sso.json`` (gitignored, not served). An empty value keeps
    the document in memory only. The file may hold ``client_secret``;
    ``public`` never returns it.
    """

    def __init__(self, path: Path | None = None, defaults_path: Path | None = None) -> None:
        self.path = path
        self.defaults_path = defaults_path
        self._lock = threading.RLock()
        self._doc: dict[str, Any] | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, *, defaults_path: Path | None = None) -> OidcConfigStore:
        source = os.environ if env is None else env
        if "CC_OIDC_CONFIG_PATH" in source:
            raw = str(source.get("CC_OIDC_CONFIG_PATH", "")).strip()
            path = None if raw == "" else Path(raw)
        else:
            path = _DEFAULT_OVERLAY
        return cls(path=path, defaults_path=defaults_path)

    def _defaults(self) -> dict[str, Any]:
        doc = _empty_doc()
        for key, value in _shipped_strings(self.defaults_path).items():
            doc[key] = value
        doc["enabled"] = False
        doc["client_secret"] = ""
        return doc

    def _read_file(self) -> dict[str, Any] | None:
        if self.path is None or not self.path.is_file():
            return None
        try:
            raw = self.path.read_bytes()
        except OSError:
            return None
        if len(raw) > _STORE_MAX_BYTES:
            return None
        try:
            loaded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(loaded, dict):
            return None
        doc = self._defaults()
        for key in _TEXT_FIELDS:
            value = loaded.get(key)
            if isinstance(value, str):
                doc[key] = value
        secret = loaded.get("client_secret", "")
        doc["client_secret"] = secret if isinstance(secret, str) else ""
        doc["enabled"] = False
        return doc

    def _load(self) -> dict[str, Any]:
        if self._doc is None:
            self._doc = self._read_file() or self._defaults()
        return self._doc

    def _write(self, doc: dict[str, Any]) -> None:
        self._doc = doc
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "issuer": doc["issuer"],
                "client_id": doc["client_id"],
                "client_secret": doc["client_secret"],
                "redirect_uri": doc["redirect_uri"],
                "scopes": doc["scopes"],
                "enabled": False,
            },
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            tmp.write_bytes(payload)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
            os.chmod(self.path, 0o600)
        except OSError:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise

    def public(self) -> dict[str, Any]:
        with self._lock:
            return _public(self._load())

    def save(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise OidcConfigError("body", "must be an object")
        _check_shape(payload)
        with self._lock:
            current = self._load()
            if "client_secret" in payload:
                secret = _secret(payload["client_secret"])
            else:
                previous = current.get("client_secret", "")
                secret = previous if isinstance(previous, str) else ""
            doc = {
                "issuer": _http_url(_require_str(payload, "issuer"), "issuer"),
                "client_id": _client_id(_require_str(payload, "client_id")),
                "client_secret": secret,
                "redirect_uri": _http_url(_require_str(payload, "redirect_uri"), "redirect_uri"),
                "scopes": _scopes(_require_str(payload, "scopes")),
                "enabled": False,
            }
            self._write(doc)
            view = _public(doc)
        view["saved"] = True
        return view

    def login_response(self) -> dict[str, Any]:
        """HTTP body for the login path. Ignores saved fields."""
        status = oidc_login(self.public())
        return {"ok": False, "status": status, "error": status, "enabled": False}
