"""Reshape raw Canvas responses into something readable.

The single most important thing here is the timezone. Canvas returns `due_at` in
UTC, and Fuqua sets deadlines at 23:59 local, so the API reports:

    "due_at": "2026-09-07T03:59:59Z"   # actually Sun 09-06 23:59 EDT

Reading the date directly puts every deadline a day late. Every timestamp exposed
to callers must go through `local()`.
"""
from __future__ import annotations

import html.parser
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from . import config

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    ZoneInfo = None  # type: ignore[assignment]

WEEKDAY_CN = "一二三四五六日"


def tz() -> timezone:
    name = config.timezone_name()
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)  # type: ignore[return-value]
        except Exception:  # noqa: BLE001 - a bad tz name falls back to UTC rather
            pass                # than taking down the whole server
    return timezone.utc


def parse(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None


def now() -> datetime:
    return datetime.now(timezone.utc)


def local(iso: str | None) -> dict | None:
    """UTC string -> several local-time representations plus a day offset.

    Returns None when the field simply has no deadline, which is common in Canvas.
    """
    dt = parse(iso)
    if dt is None:
        return None
    loc = dt.astimezone(tz())
    days = (loc.date() - now().astimezone(tz()).date()).days
    if days == 0:
        rel = "今天"
    elif days == 1:
        rel = "明天"
    elif days > 0:
        rel = f"{days} 天后"
    else:
        rel = f"逾期 {-days} 天"
    return {
        "local": loc.strftime("%Y-%m-%d %H:%M"),
        "weekday": f"周{WEEKDAY_CN[loc.weekday()]}",
        "days_from_now": days,
        "relative": rel,
        "utc": iso,
    }


class _Stripper(html.parser.HTMLParser):
    """Canvas description fields are HTML. Keep the text, insert breaks between
    blocks."""

    BREAK = {"p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in self.BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag in self.BREAK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)

    def text(self) -> str:
        joined = "".join(self.parts)
        joined = re.sub(r"[ \t ]+", " ", joined)
        joined = re.sub(r"\n\s*\n\s*\n+", "\n\n", joined)
        return joined.strip()


def plain(raw: str | None, limit: int = 4000) -> str:
    """HTML -> plain text. Truncated, so one assignment cannot flood the context
    window."""
    if not raw:
        return ""
    parser = _Stripper()
    try:
        parser.feed(raw)
        parser.close()
        text = parser.text()
    except Exception:  # noqa: BLE001 - malformed HTML falls back to blunt tag strip
        text = re.sub(r"<[^>]+>", " ", raw)
        text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[:limit] + f"\n…（已截断，原文 {len(text)} 字符）"
    return text


# ---------------------------------------------------------------- shaping

def course(raw: dict) -> dict:
    term = (raw.get("term") or {}).get("name")
    out = {
        "id": raw.get("id"),
        "name": raw.get("name"),
        "code": raw.get("course_code"),
        "term": term,
    }
    for enr in raw.get("enrollments") or []:
        score = enr.get("computed_current_score")
        if score is not None:
            out["current_score"] = score
            out["current_grade"] = enr.get("computed_current_grade")
            break
    return {k: v for k, v in out.items() if v is not None}


def assignment(raw: dict, course_name: str | None = None, detail: bool = False) -> dict:
    sub = raw.get("submission") or {}
    out: dict[str, Any] = {
        "id": raw.get("id"),
        "name": raw.get("name"),
        "course_id": raw.get("course_id"),
        "due": local(raw.get("due_at")),
        "points_possible": raw.get("points_possible"),
        "url": raw.get("html_url"),
    }
    if course_name:
        out["course"] = course_name
    if raw.get("submission_types"):
        out["submit_via"] = raw["submission_types"]

    if sub:
        out["submitted"] = bool(sub.get("submitted_at"))
        if sub.get("submitted_at"):
            out["submitted_at"] = local(sub["submitted_at"])
        if sub.get("score") is not None:
            out["score"] = sub["score"]
        if sub.get("grade") is not None:
            out["grade"] = sub["grade"]
        if sub.get("late"):
            out["late"] = True
        if sub.get("missing"):
            out["missing"] = True

    if detail:
        out["description"] = plain(raw.get("description"))
        if raw.get("lock_at"):
            out["lock_at"] = local(raw["lock_at"])
        if raw.get("allowed_attempts") not in (None, -1):
            out["allowed_attempts"] = raw["allowed_attempts"]
    return {k: v for k, v in out.items() if v is not None}


def planner_item(raw: dict) -> dict:
    """planner/items merges assignments, calendar events, announcements and
    quizzes into one stream.

    The one consistent timestamp is `plannable_date`. Each type's own field
    (due_at / start_at / posted_at) is nested under `plannable` with a different
    shape per type, so always key off `plannable_date`.
    """
    inner = raw.get("plannable") or {}
    kind = raw.get("plannable_type") or "?"
    out: dict[str, Any] = {
        "type": kind,
        "title": inner.get("title") or inner.get("name"),
        "when": local(raw.get("plannable_date")),
        "course": raw.get("context_name"),
        "course_id": raw.get("course_id"),
        "url": raw.get("html_url"),
    }
    if inner.get("points_possible") is not None:
        out["points_possible"] = inner["points_possible"]

    sub = raw.get("submissions")
    # Entries with no notion of submission (calendar events, announcements) send
    # False here rather than an object
    if isinstance(sub, dict):
        out["submitted"] = bool(sub.get("submitted"))
        for flag in ("missing", "late", "graded"):
            if sub.get(flag):
                out[flag] = True
    return {k: v for k, v in out.items() if v is not None}


def announcement(raw: dict) -> dict:
    return {k: v for k, v in {
        "id": raw.get("id"),
        "title": raw.get("title"),
        "posted_at": local(raw.get("posted_at")),
        "author": (raw.get("author") or {}).get("display_name"),
        "url": raw.get("html_url"),
        "body": plain(raw.get("message"), limit=1500),
    }.items() if v not in (None, "")}


def window(days: int) -> tuple[str, str]:
    """Inclusive date window for the planner endpoint, computed in local time."""
    today = now().astimezone(tz()).date()
    return today.isoformat(), (today + timedelta(days=days)).isoformat()
