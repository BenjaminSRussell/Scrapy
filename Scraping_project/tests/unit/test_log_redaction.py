"""#680: synthetic secrets never appear verbatim in logs (normal, warning, exception, structured)."""

import logging

import pytest

import src  # noqa: F401  (installs redaction, as every entrypoint does)
from src import log_redaction as lr

# Synthetic values only, never real credentials.
TOKEN = "ghp_SYNTHETICtoken1234567890abcdef"
BEARER = "eyJhbGciOiJIUzI1NiJ9.synthetic.payload"
COOKIE = "sessionid=SYNTHsess42; csrftoken=SYNTHcsrf99"
PASSWORD = "Synth3tic-Pa55word!"
ENV_SECRET = "envSYNTHsecretVALUE77"


@pytest.fixture
def capture():
    """A plain handler with a plain formatter, like Scrapy's/basicConfig root handlers."""
    records = []

    class _H(logging.Handler):
        def emit(self, record):
            records.append(self.format(record))

    handler = _H()
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s %(extra_field)s"))
    logger = logging.getLogger("src.test_redaction")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    yield logger, records
    logger.removeHandler(handler)


def _assert_clean(lines):
    text = "\n".join(lines)
    for secret in (TOKEN, BEARER, "SYNTHsess42", "SYNTHcsrf99", PASSWORD, ENV_SECRET):
        assert secret not in text, f"leaked {secret!r} in: {text}"
    assert lr.REDACTED in text


def test_install_is_active_after_importing_src():
    assert getattr(logging.Logger.makeRecord, "__wrapped__", None) is not None


@pytest.mark.parametrize(
    "line",
    [
        "GET https://api.example.edu/x headers={'Authorization': 'Bearer %s'}" % BEARER,
        "request headers {b'Authorization': [b'token %s'], b'Cookie': [b'%s']}" % (TOKEN, COOKIE),
        "Set-Cookie: %s" % COOKIE,
        "connecting to postgresql://scraper:%s@db:5432/uconn" % PASSWORD,
        "config loaded: password=%s api_key=%s" % (PASSWORD, TOKEN),
        '{"db_password": "%s", "access_token": "%s"}' % (PASSWORD, TOKEN),
    ],
)
def test_redact_patterns(line):
    out = lr.redact(line)
    for secret in (TOKEN, BEARER, "SYNTHsess42", "SYNTHcsrf99", PASSWORD):
        assert secret not in out
    assert lr.REDACTED in out


def test_normal_and_warning_paths_with_args(capture):
    logger, lines = capture
    logger.info("fetch %s with Authorization: Bearer %s", "https://x.edu", BEARER, extra={"extra_field": ""})
    logger.warning("retrying, password=%s", PASSWORD, extra={"extra_field": ""})
    _assert_clean(lines)
    assert lines[0].startswith("INFO") and lines[1].startswith("WARNING")


def test_exception_path(capture):
    logger, lines = capture
    try:
        raise ConnectionError(f"redis://default:{PASSWORD}@redis:6379/0 refused; token={TOKEN}")
    except ConnectionError:
        logger.exception("Redis connect failed", extra={"extra_field": ""})
    _assert_clean(lines)
    assert "Traceback" in lines[0] and "ConnectionError" in lines[0]


def test_structured_extra_fields(capture):
    logger, lines = capture
    logger.error("item error", extra={"extra_field": f"cookie: {COOKIE}"})
    _assert_clean(lines)


def test_env_secret_values_are_redacted(capture, monkeypatch):
    logger, lines = capture
    monkeypatch.setenv("KAFKA_SASL_PASSWORD", ENV_SECRET)
    lr.refresh_env_secrets()
    try:
        logger.info("sasl config user=svc pass %s", ENV_SECRET, extra={"extra_field": ""})
        _assert_clean(lines)
    finally:
        monkeypatch.delenv("KAFKA_SASL_PASSWORD")
        lr.refresh_env_secrets()


def test_pipeline_and_spider_loggers_are_covered(caplog):
    """Representative src loggers: a pipeline drop message and a spider error."""
    from src.pipelines import DataValidationPipeline

    with caplog.at_level(logging.DEBUG):
        logging.getLogger("src.pipelines").warning(
            "Dropped item %s", {"url": "https://u.edu", "auth_token": TOKEN}
        )
        logging.getLogger("scout").error("Spider error: Cookie: %s", COOKIE)
    text = caplog.text + "".join(r.getMessage() for r in caplog.records)
    assert TOKEN not in text and "SYNTHsess42" not in text
    assert DataValidationPipeline  # module imported under redaction


def test_non_secret_text_is_untouched():
    msg = "Saved 100 items to stage2_queue from https://uconn.edu/admissions?page=2"
    assert lr.redact(msg) == msg
