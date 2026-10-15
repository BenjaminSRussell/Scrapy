"""Relative links in tracked Markdown files resolve (#847). The checker is stdlib only and does not fetch http(s) by default."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_links.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_links", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


checker = _load()
INDEX = checker.build_index(
    [
        "README.md",
        "src/x.py",
        "docs/a.md",
        "docs/b.md",
        "docs/my file.md",
        "docs/sub/c.md",
        "docs/img/logo.png",
    ]
)


def _tracked_or_skip() -> list[str]:
    try:
        return checker.git_tracked(checker.REPO)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout; cannot list tracked files")


def test_repository_markdown_links_resolve():
    report = checker.check_links(checker.REPO, _tracked_or_skip())
    assert report.problems == []
    assert report.files > 0 and report.relative > 0  # an empty listing must not pass silently


def test_cli_exits_zero_on_this_checkout():
    _tracked_or_skip()
    result = subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True, cwd=checker.REPO, timeout=120
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "problem(s)" in result.stdout


@pytest.mark.parametrize(
    ("text", "broken"),
    [
        ("[b](b.md)", False),
        ("[up](../README.md)", False),
        ("[dir](sub/)", False),
        ("[sibling dir](../src)", False),
        ("[root](/README.md)", False),
        ("[encoded](my%20file.md)", False),
        ('[titled](b.md "A title")', False),
        ("[angle](<b.md>)", False),
        ("[anchor](b.md#section)", False),
        ("![logo](img/logo.png)", False),
        ('<img src="img/logo.png" alt="">', False),
        ('<a href="b.md">b</a>', False),
        ("[ref]: b.md", False),
        ("[missing](nope.md)", True),
        ("[anchor](nope.md#section)", True),
        ("![gone](img/gone.png)", True),
        ('<a href="nope.md">x</a>', True),
        ("[ref]: nope.md", True),
        ("[escape](../../../etc/passwd)", True),
    ],
)
def test_relative_targets_must_be_tracked(text, broken):
    problems, _, _ = checker.scan_markdown("docs/a.md", text, INDEX)
    assert bool(problems) is broken, problems


@pytest.mark.parametrize(
    "text",
    [
        "[mail](mailto:someone@example.com)",
        "[same page](#top)",
        "[query only](?tab=2)",
        "[protocol relative](//example.com/x)",
        "[empty]()",
        "<https://example.com>",
    ],
)
def test_non_file_targets_are_not_checked(text):
    assert checker.scan_markdown("docs/a.md", text, INDEX) == ([], 0, [])


def test_code_and_comments_are_ignored():
    text = (
        "```\n[fenced](nope.md)\n```\n"
        "~~~text\n[tilde](nope.md)\n~~~\n"
        "Use `[inline](nope.md)` here.\n"
        "<!-- [commented](nope.md)\n   still a comment -->\n"
        "[real](nope.md)\n"
    )
    problems, _, _ = checker.scan_markdown("docs/a.md", text, INDEX)
    assert len(problems) == 1 and problems[0].startswith("docs/a.md:10: broken link nope.md")


def test_footnote_definitions_are_not_links():
    problems, checked, _ = checker.scan_markdown("docs/a.md", "Text[^1].\n\n[^1]: a note with words\n", INDEX)
    assert problems == [] and checked == 0


def test_problem_message_gives_the_line_number():
    problems, _, _ = checker.scan_markdown("docs/a.md", "one\n\n\n[x](missing.md)\n", INDEX)
    assert problems == ["docs/a.md:4: broken link missing.md (no tracked file or directory at docs/missing.md)"]


def test_http_links_are_not_fetched_by_default(tmp_path):
    (tmp_path / "a.md").write_text("[x](https://example.com/gone)\n", encoding="utf-8")

    def refuse(url):
        raise AssertionError(f"fetched {url} without --external")

    report = checker.check_links(tmp_path, ["a.md"], probe=refuse)
    assert report.problems == [] and report.urls == 1


def test_external_mode_reports_only_urls_that_fail(tmp_path):
    (tmp_path / "a.md").write_text("[ok](https://ok.example/)\n\n[bad](https://bad.example/)\n", encoding="utf-8")
    answers = {"https://ok.example/": None, "https://bad.example/": "HTTP 404"}
    report = checker.check_links(tmp_path, ["a.md"], external=True, probe=answers.__getitem__)
    assert report.problems == ["a.md:3: https://bad.example/ did not answer (HTTP 404)"]


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_probe_url_accepts_a_successful_answer(monkeypatch):
    monkeypatch.setattr(checker.urllib.request, "urlopen", lambda request, timeout: _Response())
    assert checker.probe_url("https://ok.example/") is None


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (urllib.error.HTTPError("https://bad.example/", 404, "Not Found", {}, None), "HTTP 404"),
        (urllib.error.URLError("name resolution failed"), "name resolution failed"),
    ],
)
def test_probe_url_reports_failures(monkeypatch, error, reason):
    def fail(request, timeout):
        raise error

    monkeypatch.setattr(checker.urllib.request, "urlopen", fail)
    assert checker.probe_url("https://bad.example/") == reason


def test_main_exit_codes(tmp_path, monkeypatch, capsys):
    (tmp_path / "a.md").write_text("[b](b.md)\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("# b\n", encoding="utf-8")
    monkeypatch.setattr(checker, "git_tracked", lambda root: ["a.md", "b.md"])
    assert checker.main(["--root", str(tmp_path)]) == 0

    (tmp_path / "a.md").write_text("[b](gone.md)\n", encoding="utf-8")
    assert checker.main(["--root", str(tmp_path)]) == 1
    assert "a.md:1: broken link gone.md" in capsys.readouterr().out


def test_main_exits_2_when_git_cannot_list_files(monkeypatch, capsys):
    def no_git(root):
        raise subprocess.CalledProcessError(128, ["git", "ls-files"])

    monkeypatch.setattr(checker, "git_tracked", no_git)
    assert checker.main([]) == 2
    assert "cannot list tracked files" in capsys.readouterr().err
