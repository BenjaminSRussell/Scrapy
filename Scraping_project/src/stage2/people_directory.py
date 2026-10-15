"""Faculty/people directory parser (#642).

``PEOPLE_DIRECTORY_PARSE_ENABLED`` defaults off. When a caller passes
``enabled=True`` (the Stage 2 analyzer does that only while the flag is on),
a page contributes a ``PersonItem`` only when one short card has:

* a schema.org Person, h-card/vcard, or directory-card class
* one person-shaped name
* one faculty or staff title
* one email that matches that name

The structure score must be at least ``PersonItem.MIN_CONFIDENCE``. Prose,
generic mailboxes, ambiguous cards, and long pages are skipped.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any
from urllib.parse import unquote

from bs4 import BeautifulSoup

from src.items import PersonItem
from src.utils.feature_flags import get_bool

PEOPLE_DIRECTORY_PARSE_ENABLED = "PEOPLE_DIRECTORY_PARSE_ENABLED"

_MAX_CARD_CHARS = 900
_EMIT_SCORE = round(PersonItem.MIN_CONFIDENCE * 100)
_STRUCTURE_SCHEMA = 50
_STRUCTURE_VCARD = 45
_STRUCTURE_CARD = 35
_STRUCTURE_LOOSE = 20
_POINTS_MARKED_NAME = 20
_POINTS_EMAIL = 15
_POINTS_MARKED_TITLE = 20

_VCARD_CLASSES = frozenset({"vcard", "h-card", "hcard"})
_STRONG_CARD = frozenset({
    "person-card",
    "faculty-card",
    "profile-card",
    "directory-card",
    "staff-card",
    "faculty-member",
    "staff-member",
    "directory-entry",
})
_LOOSE_CARD = frozenset({"person", "profile"})
_NAME_CLASSES = frozenset({
    "fn",
    "p-name",
    "person-name",
    "faculty-name",
    "profile-name",
    "directory-name",
})
_TITLE_CLASSES = frozenset({
    "p-job-title",
    "job-title",
    "jobtitle",
    "faculty-title",
    "person-title",
    "rank",
})
_VCARD_TITLE_CLASSES = frozenset({"title", "role"})
_EMAIL_CLASSES = frozenset({"email", "u-email", "p-email", "person-email"})

_GENERIC_LOCAL = frozenset({
    "info", "contact", "contacts", "webmaster", "admin", "administrator",
    "admissions", "office", "help", "support", "noreply", "no-reply",
    "donotreply", "department", "dept", "faculty", "staff", "directory",
    "registrar", "hr", "communications", "media", "press", "news",
    "feedback", "hello", "team", "dean", "deansoffice", "chair",
    "undergrad", "graduate", "students", "mailbox", "email", "mail",
    "jobs", "careers", "security", "privacy", "postmaster", "root",
    "abuse", "marketing", "events", "calendar", "library", "helpdesk",
    "service", "services", "general", "enquiries", "inquiries",
})
_NAME_STOP = frozenset({
    "university", "department", "dept", "office", "college", "school", "faculty",
    "staff", "directory", "contact", "admissions", "committee", "center", "centre",
    "institute", "program", "programme", "services", "service", "information",
    "webmaster", "news", "about", "undergraduate", "graduate", "students", "student",
    "homepage", "welcome", "research", "professor", "lecturer", "instructor",
})
_TITLE_BAN = frozenset({
    "published", "today", "please", "click", "contact", "http", "https", "www",
    "teaches", "advises", "joined", "welcome", "homepage", "news", "read",
})

_PREFIX = re.compile(r"^(?:(?:dr|prof|mr|mrs|ms|mx|miss)\.?\s+)+", re.IGNORECASE)
_SUFFIX = re.compile(
    r"(?:,?\s+(?:ph\.?d\.?|m\.?d\.?|ed\.?d\.?|j\.?d\.?|m\.?s\.?|m\.?a\.?|b\.?s\.?|b\.?a\.?|jr\.?|sr\.?|ii|iii|iv))+$",
    re.IGNORECASE,
)
_TITLE_RE = re.compile(
    r"^(?:"
    r"(?:(?:associate|assistant|adjunct|clinical|visiting|research|emeritus|distinguished|full)\s+){0,2}"
    r"(?:professor|lecturer|instructor|dean|chair|chairperson|director|librarian)"
    r"(?:\s+emerit(?:us|a))?"
    r"(?:\s+and\s+(?:chair|chairperson|director|dean|professor))?"
    r"(?:\s+(?:of|for)\s+[a-z][\w'’\-]*(?:\s+(?:and|&)\s+[a-z][\w'’\-]*|\s+[a-z][\w'’\-]*){0,6})?"
    r"|"
    r"(?:department|dept\.)\s+(?:head|chair)"
    r"|"
    r"head of (?:the )?department"
    r")$",
    re.IGNORECASE,
)


def extract_people(html: str, source_url: str, *, enabled: bool | None = None) -> list[PersonItem]:
    """High-confidence people on one page. Empty when the mode is off or nothing qualifies."""
    if enabled is None:
        enabled = get_bool(PEOPLE_DIRECTORY_PARSE_ENABLED, False)
    if not enabled or not isinstance(html, str) or not html.strip():
        return []
    page_url = source_url.strip() if isinstance(source_url, str) else ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(["script", "style", "noscript", "template"]):
        tag.decompose()

    best: dict[tuple[str, str], PersonItem] = {}
    for card in _cards(soup):
        item = _parse_card(card, page_url)
        if item is None:
            continue
        key = (str(item["name"]).casefold(), str(item["email"]))
        previous = best.get(key)
        if previous is None or float(item["confidence"]) > float(previous["confidence"]):
            best[key] = item
    rows = list(best.values())
    rows.sort(key=lambda item: (str(item["name"]).casefold(), str(item["email"])))
    return rows


def _cards(soup: Any) -> list[Any]:
    return _innermost([el for el in soup.find_all(True) if _is_card(el)])


def _is_card(el: Any) -> bool:
    if "person" in _itemtypes(el):
        return True
    classes = _class_tokens(el)
    if classes & (_VCARD_CLASSES | _STRONG_CARD):
        return True
    return bool(classes & _LOOSE_CARD and _has_mailto(el))


def _parse_card(card: Any, source_url: str) -> PersonItem | None:
    if _text_len(card) > _MAX_CARD_CHARS:
        return None
    structure, kind = _structure(card)
    if structure <= 0:
        return None
    name_hit = _name_from_card(card)
    if name_hit is None:
        return None
    name, name_marked = name_hit
    title_hit = _title_from_card(card, vcard_titles=kind == "vcard")
    if title_hit is None:
        return None
    title, title_marked = title_hit
    if _fold(title) == _fold(name):
        return None
    matched = _matched_emails(card, name)
    if len(matched) != 1:
        return None
    score = structure + _POINTS_EMAIL
    if name_marked:
        score += _POINTS_MARKED_NAME
    if title_marked:
        score += _POINTS_MARKED_TITLE
    if score < _EMIT_SCORE:
        return None
    item = PersonItem(
        name=name,
        email=matched[0],
        title=title,
        source_url=source_url,
        confidence=min(score, 100) / 100.0,
    )
    item.validate()
    return item


def _name_from_card(card: Any) -> tuple[str, bool] | None:
    marked = _innermost([node for node in card.find_all(True) if _is_name_node(node)])
    if marked:
        names = _unique_person_names(marked)
        if len(names) != 1:
            return None
        return names[0], True
    headings = _innermost(list(card.find_all(["h1", "h2", "h3", "h4"])))
    names = _unique_person_names(headings)
    if len(names) != 1:
        return None
    return names[0], False


def _title_from_card(card: Any, *, vcard_titles: bool) -> tuple[str, bool] | None:
    marked = _innermost([
        node for node in card.find_all(True) if _is_title_node(node, vcard_titles=vcard_titles)
    ])
    if marked:
        titles = _unique_titles(marked)
        if len(titles) != 1:
            return None
        return titles[0], True
    candidates = [node for node in card.find_all(True) if _title_ok(_node_text(node))]
    titles = _unique_titles(_innermost(candidates))
    if len(titles) != 1:
        return None
    return titles[0], False


def _matched_emails(card: Any, name: str) -> list[str]:
    found: list[str] = []
    for email in _emails(card):
        if _email_matches_name(email, name) and email not in found:
            found.append(email)
    return found


def _emails(card: Any) -> list[str]:
    found: list[str] = []
    for node in card.find_all(True):
        href = node.get("href")
        if isinstance(href, str):
            _push_email(found, href)
        if "email" in _prop_set(node) or _class_tokens(node) & _EMAIL_CLASSES:
            content = node.get("content")
            if isinstance(content, str):
                _push_email(found, content)
            _push_email(found, node.get_text(" ", strip=True))
    return found


def _push_email(found: list[str], raw: str) -> None:
    email = _parse_email(raw)
    if email and email not in found:
        found.append(email)


def _parse_email(raw: str) -> str | None:
    text = raw.strip()
    if not text:
        return None
    if text.lower().startswith("mailto:"):
        text = text[7:]
    text = unquote(text).split("?", 1)[0].split(",", 1)[0].strip().strip("<>").strip().lower()
    if PersonItem.EMAIL_RE.fullmatch(text) is None:
        return None
    return text


def _email_matches_name(email: str, name: str) -> bool:
    local = email.split("@", 1)[0].split("+", 1)[0].strip().lower()
    if not local:
        return False
    folded_local = _fold(local)
    if folded_local in _GENERIC_LOCAL:
        return False
    head = re.split(r"[._-]", folded_local, maxsplit=1)[0]
    if head in _GENERIC_LOCAL:
        return False
    tokens = _name_tokens(name)
    if len(tokens) < 2:
        return False
    first, last = tokens[0], tokens[-1]
    parts = [part for part in re.split(r"[._-]+", folded_local) if part]
    if len(parts) >= 2:
        if not all(part.isalpha() for part in parts):
            return False
        has_first = any(
            part == first or part.startswith(first) or (first.startswith(part) and len(part) >= 2)
            for part in parts
        )
        has_last = any(
            part == last or part.startswith(last) or (last.startswith(part) and len(part) >= 3)
            for part in parts
        )
        return has_first and has_last
    compact = "".join(parts)
    if not compact.isalpha():
        return False
    return compact in {first[0] + last, first + last, last + first[0], first + last[0]}


def _person_name(text: str) -> str | None:
    cleaned = _squash(text)
    cleaned = _PREFIX.sub("", cleaned)
    cleaned = _SUFFIX.sub("", cleaned).strip(" ,")
    if not cleaned:
        return None
    parts = cleaned.split(" ")
    if not 2 <= len(parts) <= 4:
        return None
    initials = 0
    words = 0
    for part in parts:
        if _fold(part) in _NAME_STOP:
            return None
        if re.fullmatch(r"[A-Z]\.?", part):
            initials += 1
            continue
        if not _name_word(part):
            return None
        words += 1
    if words < 2 or initials > 1:
        return None
    return cleaned


def _name_word(token: str) -> bool:
    if len(token) < 2 or not token[0].isupper():
        return False
    if any(ch.isdigit() for ch in token):
        return False
    if not all(ch.isalpha() or ch in "'’-." for ch in token):
        return False
    letters = [ch for ch in token if ch.isalpha()]
    return len(letters) >= 2 and any(ch.islower() for ch in letters)


def _title_ok(text: str) -> str | None:
    cleaned = _squash(text)
    if not cleaned or len(cleaned) > 80 or _TITLE_RE.fullmatch(cleaned) is None:
        return None
    tokens = {tok.casefold().strip(".,") for tok in cleaned.split()}
    if tokens & _TITLE_BAN:
        return None
    return cleaned


def _unique_person_names(nodes: list[Any]) -> list[str]:
    names: list[str] = []
    for node in nodes:
        name = _person_name(_node_text(node))
        if name and name not in names:
            names.append(name)
    return names


def _unique_titles(nodes: list[Any]) -> list[str]:
    titles: list[str] = []
    for node in nodes:
        title = _title_ok(_node_text(node))
        if title and title not in titles:
            titles.append(title)
    return titles


def _is_name_node(node: Any) -> bool:
    return bool(_prop_set(node) & {"name"} or _class_tokens(node) & _NAME_CLASSES)


def _is_title_node(node: Any, *, vcard_titles: bool) -> bool:
    classes = _TITLE_CLASSES | (_VCARD_TITLE_CLASSES if vcard_titles else frozenset())
    return "jobtitle" in _prop_set(node) or bool(_class_tokens(node) & classes)


def _structure(card: Any) -> tuple[int, str]:
    if "person" in _itemtypes(card):
        return _STRUCTURE_SCHEMA, "schema"
    classes = _class_tokens(card)
    if classes & _VCARD_CLASSES:
        return _STRUCTURE_VCARD, "vcard"
    if classes & _STRONG_CARD:
        return _STRUCTURE_CARD, "card"
    if classes & _LOOSE_CARD:
        return _STRUCTURE_LOOSE, "loose"
    return 0, ""


def _itemtypes(el: Any) -> set[str]:
    raw = el.get("itemtype") or ""
    if isinstance(raw, list):
        raw = " ".join(str(part) for part in raw)
    found: set[str] = set()
    for part in str(raw).replace(",", " ").split():
        token = part.split("#", 1)[0].rstrip("/").rsplit("/", 1)[-1].strip().lower()
        if token:
            found.add(token)
    return found


def _class_tokens(el: Any) -> set[str]:
    raw = el.get("class") or []
    if isinstance(raw, str):
        parts = raw.split()
    elif isinstance(raw, list):
        parts = [str(part) for part in raw]
    else:
        parts = []
    return {part.lower() for part in parts}


def _prop_set(el: Any) -> set[str]:
    raw = el.get("itemprop") or ""
    if isinstance(raw, list):
        raw = " ".join(str(part) for part in raw)
    return {part.lower() for part in str(raw).split()}


def _node_text(node: Any) -> str:
    content = node.get("content")
    if isinstance(content, str) and content.strip():
        return content
    return str(node.get_text(" ", strip=True))


def _has_mailto(el: Any) -> bool:
    for node in el.find_all("a", href=True):
        href = node.get("href")
        if isinstance(href, str) and href.lower().startswith("mailto:"):
            return True
    return False


def _text_len(el: Any) -> int:
    return len(_squash(str(el.get_text(" ", strip=True))))


def _innermost(nodes: list[Any]) -> list[Any]:
    return [node for node in nodes if not any(node in other.parents for other in nodes)]


def _name_tokens(name: str) -> list[str]:
    tokens: list[str] = []
    cleaned = name.replace("'", "").replace("’", "").replace("-", " ")
    for raw in cleaned.split():
        folded = _fold(raw).rstrip(".")
        if len(folded) >= 2 and folded.isalpha():
            tokens.append(folded)
    return tokens


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return stripped.casefold()


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
