#!/usr/bin/env python3
"""Check relative links in the git-tracked Markdown files (#847).

Stdlib only. The file list comes from `git ls-files`, so the check sees what GitHub
renders and ignores untracked or ignored files in a local checkout. A relative link
is broken when its path is not a tracked file or directory, or when it leaves the
repository. Anchors (`#section`) and query strings are not checked. Links inside code
spans, fenced code blocks and HTML comments are ignored; indented code blocks are not
recognised.

http(s) links are not fetched unless `--external` is given, so CI needs no network.
With `--external` each http(s) URL is requested once with GET; an answer of HTTP 400
or more, or a network error, is reported.

Exit 0 when every link resolves, 1 when any does not, 2 when git cannot list files.
"""

from __future__ import annotations

import argparse
import http.client
import posixpath
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import NamedTuple
from urllib.parse import unquote

REPO = Path(__file__).resolve().parents[2]
USER_AGENT = "scrapy-check-links/1.0"

SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
CODE_SPAN = re.compile(r"(`+).*?\1")
FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
INLINE_LINK = re.compile(
    r"""!?\[(?:[^\[\]]|\[[^\[\]]*\])*\]\(\s*(<[^>]*>|[^)\s]*)(?:\s+(?:"[^"]*"|'[^']*'|\([^)]*\)))?\s*\)"""
)
REF_DEF = re.compile(r"^ {0,3}\[([^\]]+)\]:\s*(<[^>]*>|\S+)")
HTML_LINK = re.compile(r"""<(?:a|img)\b[^>]*?\s(?:href|src)\s*=\s*["']([^"']*)["']""", re.IGNORECASE)


class Report(NamedTuple):
    problems: list[str]
    files: int
    relative: int
    urls: int


def git_tracked(root: Path) -> list[str]:
    """Paths of every file git tracks under `root`, relative to it, with forward slashes."""
    listing = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout
    return [path for path in listing.split("\0") if path]


def build_index(tracked: Iterable[str]) -> set[str]:
    """Tracked paths plus every directory that contains one. The empty string is the repository root."""
    index = {""}
    for path in tracked:
        index.add(path)
        parent = posixpath.dirname(path)
        while parent and parent not in index:
            index.add(parent)
            parent = posixpath.dirname(parent)
    return index


def iter_targets(text: str) -> Iterator[tuple[int, str]]:
    """Yield (line number, raw target) for each link or image rendered outside code and comments."""
    # Blank each comment but keep its newlines, so line numbers still match the file.
    lines = COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), text).split("\n")
    fence = ""
    for number, line in enumerate(lines, 1):
        if fence:
            closing = line.strip()
            if closing and set(closing) == {fence[0]} and len(closing) >= len(fence):
                fence = ""
            continue
        opening = FENCE.match(line)
        if opening:
            fence = opening.group(1)
            continue
        visible = CODE_SPAN.sub("", line)
        for target in [*INLINE_LINK.findall(visible), *HTML_LINK.findall(visible)]:
            yield number, target
        definition = REF_DEF.match(line)
        if definition and not definition.group(1).startswith("^"):  # "[^1]: note" is a footnote, not a link
            yield number, definition.group(2)


def classify(target: str) -> tuple[str, str]:
    """('url', u) for http(s), ('path', p) for a path to check, or ('skip', '') for anything else."""
    bare = target[1:-1] if target.startswith("<") and target.endswith(">") else target
    if bare.lower().startswith(("http://", "https://")):
        return "url", bare
    if not bare or bare.startswith(("#", "//")) or SCHEME.match(bare):
        return "skip", ""
    path = unquote(bare.split("#", 1)[0].split("?", 1)[0])
    return ("path", path) if path else ("skip", "")


def resolve(md_rel: str, path: str) -> str | None:
    """Repository-relative location of `path` as seen from the Markdown file `md_rel`.

    A leading "/" means the repository root, as on GitHub. Returns None when the path
    leaves the repository. The empty string is the repository root itself.
    """
    base = "" if path.startswith("/") else posixpath.dirname(md_rel)
    location = posixpath.normpath(posixpath.join(base, path.lstrip("/")))
    if location == ".":
        return ""
    if location == ".." or location.startswith("../"):
        return None
    return location


def scan_markdown(md_rel: str, text: str, index: set[str]) -> tuple[list[str], int, list[tuple[int, str]]]:
    """Check one file. Returns (problems, relative links checked, (line, url) for each http(s) link)."""
    problems: list[str] = []
    checked = 0
    urls: list[tuple[int, str]] = []
    for number, target in iter_targets(text):
        kind, value = classify(target)
        if kind == "url":
            urls.append((number, value))
        elif kind == "path":
            checked += 1
            location = resolve(md_rel, value)
            if location is None:
                problems.append(f"{md_rel}:{number}: link {target} leaves the repository")
            elif location not in index:
                problems.append(
                    f"{md_rel}:{number}: broken link {target} (no tracked file or directory at {location or '.'})"
                )
    return problems, checked, urls


def probe_url(url: str, timeout: float = 10.0) -> str | None:
    """None when the URL answers below HTTP 400, otherwise a short reason. The response body is not read."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout):
            return None
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code}"
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as exc:
        return str(getattr(exc, "reason", exc))


def check_links(
    root: Path,
    tracked: Iterable[str],
    *,
    external: bool = False,
    probe: Callable[[str], str | None] = probe_url,
) -> Report:
    """Check every tracked Markdown file under `root`. `probe` is used only when `external` is set."""
    tracked = list(tracked)
    index = build_index(tracked)
    problems: list[str] = []
    files = checked = 0
    urls: dict[str, list[str]] = {}
    for md_rel in tracked:
        if not md_rel.lower().endswith(".md"):
            continue
        files += 1
        try:
            text = (root / md_rel).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            problems.append(f"{md_rel}: cannot read file ({exc.strerror or exc})")
            continue
        file_problems, file_checked, file_urls = scan_markdown(md_rel, text, index)
        problems.extend(file_problems)
        checked += file_checked
        for number, url in file_urls:
            urls.setdefault(url, []).append(f"{md_rel}:{number}")
    if external:
        for url, places in sorted(urls.items()):
            reason = probe(url)
            if reason:
                problems.extend(f"{place}: {url} did not answer ({reason})" for place in places)
    return Report(problems, files, checked, len(urls))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check relative links in tracked Markdown files (#847).")
    parser.add_argument("--root", type=Path, default=REPO, help="repository root (default: this checkout)")
    parser.add_argument("--external", action="store_true", help="also request http(s) links (default: not fetched)")
    args = parser.parse_args(argv)
    try:
        tracked = git_tracked(args.root)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"check_links: cannot list tracked files with git: {exc}", file=sys.stderr)
        return 2
    report = check_links(args.root, tracked, external=args.external)
    for problem in report.problems:
        print(problem)
    mode = "requested" if args.external else "not fetched"
    print(
        f"checked {report.relative} relative links in {report.files} markdown files; "
        f"{report.urls} http(s) links {mode}; {len(report.problems)} problem(s)"
    )
    return 1 if report.problems else 0


if __name__ == "__main__":
    sys.exit(main())
