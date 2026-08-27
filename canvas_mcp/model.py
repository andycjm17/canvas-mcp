"""把 Canvas 的原始响应整理成便于阅读的结构。

最重要的一件事是时区。Canvas 的 `due_at` 一律是 UTC，而 Fuqua 的截止时间
基本都设在本地 23:59，于是接口里长这样：

    "due_at": "2026-09-07T03:59:59Z"   # 实际是 09-06 周日 23:59 EDT

直接读日期会把每个 DDL 都记晚一天。所有对外暴露的时间都必须过 `local()`。
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
        except Exception:  # noqa: BLE001 - 时区名写错就退回 UTC，不要让 server 挂掉
            pass
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
    """UTC 字符串 -> 本地时间的多种表示，附带相对今天的天数。

    返回 None 表示这个字段本来就没有截止时间（Canvas 里很常见）。
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
    """Canvas 的描述字段是 HTML。只留纯文本，段落间补换行。"""

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
        joined = re.sub(r"[ \t ]+", " ", joined)
        joined = re.sub(r"\n\s*\n\s*\n+", "\n\n", joined)
        return joined.strip()


def plain(raw: str | None, limit: int = 4000) -> str:
    """HTML -> 纯文本。超长截断，避免把整个 context window 塞满。"""
    if not raw:
        return ""
    parser = _Stripper()
    try:
        parser.feed(raw)
        parser.close()
        text = parser.text()
    except Exception:  # noqa: BLE001 - 畸形 HTML 退回粗暴去标签
        text = re.sub(r"<[^>]+>", " ", raw)
        text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[:limit] + f"\n…（已截断，原文 {len(text)} 字符）"
    return text


# ---------------------------------------------------------------- 结构整理

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
    """planner/items 把作业、日历事件、公告、测验揉在一个流里。

    统一的时间字段是 `plannable_date`，各类型自己的时间字段（due_at / start_at /
    posted_at）藏在 `plannable` 里，形状不一致，所以以 plannable_date 为准。
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
    # 没有提交概念的条目（日历事件、公告）这里是 False 而不是对象
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
    """给 planner 用的日期窗口，闭区间，按本地时区算。"""
    today = now().astimezone(tz()).date()
    return today.isoformat(), (today + timedelta(days=days)).isoformat()
