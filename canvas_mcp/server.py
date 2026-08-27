"""Canvas LMS MCP server (stdio / JSON-RPC 2.0, standard library only).

Read-only: every tool issues GETs. It will not submit work, change grades, or post.

stdout is the protocol channel; all logging goes to stderr.
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import traceback
from typing import Any, Callable

from . import api, config, i18n, model

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "canvas", "version": "0.1.0"}


def _client() -> api.Client:
    return api.Client()


def _active_courses(client: api.Client) -> list[dict]:
    return client.paginate(
        "/courses",
        enrollment_state="active",
        **{"include[]": ["term", "total_scores"]},
    )


def _resolve_course(client: api.Client, name_or_id: Any) -> tuple[int, str]:
    """Resolve a course name (fuzzy) or id into (course_id, course_name)."""
    if name_or_id is None or name_or_id == "":
        raise ValueError(i18n.t("err.course_required"))

    courses = _active_courses(client)
    text = str(name_or_id).strip()

    if text.isdigit():
        cid = int(text)
        for c in courses:
            if c.get("id") == cid:
                return cid, c.get("name") or str(cid)
        # Allow ids missing from the active list — the course may be finished
        return cid, str(cid)

    lowered = text.lower()
    exact = [c for c in courses if (c.get("name") or "").lower() == lowered]
    matches = exact or [
        c for c in courses
        if lowered in (c.get("name") or "").lower()
        or lowered in (c.get("course_code") or "").lower()
    ]
    if not matches:
        names = "、".join(c.get("name", "?") for c in courses)
        raise ValueError(i18n.t("err.course_not_found", name=repr(text), names=names))
    if len(matches) > 1:
        names = "、".join(c.get("name", "?") for c in matches)
        raise ValueError(i18n.t("err.course_ambiguous", name=repr(text), names=names))
    return matches[0]["id"], matches[0].get("name") or str(matches[0]["id"])


# ---------------------------------------------------------------- tools

def tool_canvas_status() -> Any:
    """Connectivity self-check. Start here when something is wrong."""
    client = _client()
    me = client.get("/users/self")
    courses = _active_courses(client)
    return {
        "host": client.host,
        "user": me.get("name"),
        "user_id": me.get("id"),
        "timezone": config.timezone_name(),
        "active_courses": len(courses),
        "token_source": (i18n.t("status.token_env") if "CANVAS_MCP_TOKEN" in os.environ
                         else str(config.CONFIG_PATH)),
        "note": i18n.t("status.note"),
    }


def tool_list_courses(include_finished: bool = False) -> Any:
    """List courses. Defaults to the ones still running."""
    client = _client()
    if include_finished:
        raw = client.paginate(
            "/courses",
            **{"include[]": ["term", "total_scores"], "state[]": ["available", "completed"]},
        )
    else:
        raw = _active_courses(client)
    courses = [model.course(c) for c in raw]
    return {"count": len(courses), "courses": courses}


def tool_upcoming(days: int = 14, include_done: bool = False) -> Any:
    """Assignments, exams, calendar events and announcements for the next N days,
    sorted by time.

    Backed by /planner/items, which merges every type into one stream — cheaper
    than walking each course's assignment list.
    """
    if days < 1 or days > 180:
        raise ValueError(i18n.t("err.days_range"))
    client = _client()
    start, end = model.window(days)
    raw = client.paginate("/planner/items", start_date=start, end_date=end, limit=200)

    items = [model.planner_item(i) for i in raw]
    if not include_done:
        items = [i for i in items if not i.get("submitted")]
    items.sort(key=lambda i: (i.get("when") or {}).get("utc") or "")

    return {
        "window": f"{start} ~ {end}（{config.timezone_name()}）",
        "count": len(items),
        "items": items,
    }


def tool_overdue() -> Any:
    """Assignments past their due date that have not been submitted."""
    client = _client()
    raw = client.paginate("/users/self/todo", limit=100)
    out = []
    for item in raw:
        a = item.get("assignment") or {}
        due = model.local(a.get("due_at"))
        if due and due["days_from_now"] < 0:
            entry = model.assignment(a, course_name=item.get("context_name"))
            entry["overdue_days"] = -due["days_from_now"]
            out.append(entry)
    out.sort(key=lambda x: -x.get("overdue_days", 0))
    return {"count": len(out), "items": out}


def tool_list_assignments(course: Any = None, only_upcoming: bool = False,
                          limit: int = 100) -> Any:
    """One course's assignments, with due dates and your submission status."""
    client = _client()
    cid, cname = _resolve_course(client, course)
    raw = client.paginate(
        f"/courses/{cid}/assignments",
        order_by="due_at",
        limit=limit,
        **{"include[]": ["submission"]},
    )
    items = [model.assignment(a, course_name=cname) for a in raw]
    if only_upcoming:
        items = [
            i for i in items
            if i.get("due") and i["due"]["days_from_now"] >= 0 and not i.get("submitted")
        ]
    return {"course": cname, "course_id": cid, "count": len(items), "assignments": items}


def tool_get_assignment(course: Any, assignment_id: int) -> Any:
    """Full detail for one assignment, including the instructions body
    (HTML is converted to plain text)."""
    client = _client()
    cid, cname = _resolve_course(client, course)
    raw = client.get(
        f"/courses/{cid}/assignments/{assignment_id}",
        **{"include[]": ["submission"]},
    )
    return model.assignment(raw, course_name=cname, detail=True)


def tool_list_announcements(course: Any = None, days: int = 30, limit: int = 20) -> Any:
    """Recent course announcements. With no course, queries every active course."""
    client = _client()
    if course:
        cid, cname = _resolve_course(client, course)
        codes = [f"course_{cid}"]
        names = {cid: cname}
    else:
        courses = _active_courses(client)
        codes = [f"course_{c['id']}" for c in courses]
        names = {c["id"]: c.get("name") for c in courses}
    if not codes:
        return {"count": 0, "announcements": []}

    start, _ = model.window(0)
    start_date = (
        model.now().astimezone(model.tz()).date() - datetime.timedelta(days=days)
    ).isoformat()

    raw = client.paginate(
        "/announcements",
        limit=limit,
        start_date=start_date,
        end_date=start,
        **{"context_codes[]": codes},
    )
    out = []
    for a in raw:
        item = model.announcement(a)
        ctx = str(a.get("context_code") or "")
        if ctx.startswith("course_"):
            try:
                item["course"] = names.get(int(ctx.split("_", 1)[1]))
            except ValueError:
                pass
        out.append(item)
    out.sort(key=lambda x: (x.get("posted_at") or {}).get("utc") or "", reverse=True)
    return {"count": len(out), "announcements": out}


def tool_list_grades() -> Any:
    """Current overall score per course. Only courses with posted grades appear."""
    client = _client()
    # Grades include finished courses; querying only the active list degrades
    # their names to "course <id>".
    all_courses = client.paginate(
        "/courses",
        **{"include[]": ["term"], "state[]": ["available", "completed"]},
    )
    courses = {c["id"]: c for c in all_courses}
    enrollments = client.paginate(
        "/users/self/enrollments",
        limit=100,
        **{"state[]": ["active", "completed"]},
    )
    seen: dict[int, dict] = {}
    for e in enrollments:
        if e.get("type") != "StudentEnrollment":
            continue
        cid = e.get("course_id")
        grades = e.get("grades") or {}
        score = grades.get("current_score")
        if score is None or cid in seen:
            continue
        course_raw = courses.get(cid) or {}
        seen[cid] = {
            "course_id": cid,
            "course": course_raw.get("name") or f"course {cid}",
            "term": (course_raw.get("term") or {}).get("name"),
            "current_score": score,
            "current_grade": grades.get("current_grade"),
            "final_score": grades.get("final_score"),
        }
    out = [{k: v for k, v in row.items() if v is not None} for row in seen.values()]
    return {"count": len(out), "grades": out}


# ---------------------------------------------------------------- tool schemas

def _s(**props: Any) -> dict:
    return {"type": "object", "properties": props}


_COURSE = {"type": "string", "description": i18n.t("tool.course_param")}

TOOLS: list[dict] = [
    {
        "name": "canvas_status",
        "description": i18n.t("tool.canvas_status"),
        "inputSchema": _s(),
    },
    {
        "name": "list_courses",
        "description": i18n.t("tool.list_courses"),
        "inputSchema": _s(include_finished={
            "type": "boolean", "description": i18n.t("arg.include_finished")}),
    },
    {
        "name": "upcoming",
        "description": i18n.t("tool.upcoming"),
        "inputSchema": _s(
            days={"type": "integer", "description": i18n.t("arg.days_forward")},
            include_done={"type": "boolean", "description": i18n.t("arg.include_done")},
        ),
    },
    {
        "name": "overdue",
        "description": i18n.t("tool.overdue"),
        "inputSchema": _s(),
    },
    {
        "name": "list_assignments",
        "description": i18n.t("tool.list_assignments"),
        "inputSchema": {
            **_s(
                course=_COURSE,
                only_upcoming={"type": "boolean", "description": i18n.t("arg.only_upcoming")},
                limit={"type": "integer", "description": i18n.t("arg.limit_100")},
            ),
            "required": ["course"],
        },
    },
    {
        "name": "get_assignment",
        "description": i18n.t("tool.get_assignment"),
        "inputSchema": {
            **_s(course=_COURSE, assignment_id={"type": "integer"}),
            "required": ["course", "assignment_id"],
        },
    },
    {
        "name": "list_announcements",
        "description": i18n.t("tool.list_announcements"),
        "inputSchema": _s(
            course=_COURSE,
            days={"type": "integer", "description": i18n.t("arg.days_back")},
            limit={"type": "integer", "description": i18n.t("arg.limit_20")},
        ),
    },
    {
        "name": "list_grades",
        "description": i18n.t("tool.list_grades"),
        "inputSchema": _s(),
    },
]

HANDLERS: dict[str, Callable[..., Any]] = {
    "canvas_status": tool_canvas_status,
    "list_courses": tool_list_courses,
    "upcoming": tool_upcoming,
    "overdue": tool_overdue,
    "list_assignments": tool_list_assignments,
    "get_assignment": tool_get_assignment,
    "list_announcements": tool_list_announcements,
    "list_grades": tool_list_grades,
}


# ---------------------------------------------------------------- JSON-RPC

def _log(msg: str) -> None:
    print(f"[canvas-mcp] {msg}", file=sys.stderr, flush=True)


def _result(rid: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _error(rid: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def _call_tool(name: str, args: dict) -> dict:
    handler = HANDLERS.get(name)
    if handler is None:
        return {"content": [{"type": "text", "text": i18n.t("err.unknown_tool", name=name)}],
                "isError": True}
    try:
        out = handler(**args)
        text = json.dumps(out, ensure_ascii=False, indent=2)
        return {"content": [{"type": "text", "text": text}]}
    except (api.ApiError, config.ConfigError, ValueError) as e:
        return {"content": [{"type": "text", "text": str(e)}], "isError": True}
    except TypeError as e:
        return {"content": [{"type": "text", "text": i18n.t("err.bad_args", error=e)}],
                "isError": True}
    except Exception as e:  # noqa: BLE001 - last resort, never take down the server
        _log(traceback.format_exc())
        return {"content": [{"type": "text", "text": i18n.t("err.internal", error=e)}],
                "isError": True}


def handle(msg: dict) -> dict | None:
    method, rid = msg.get("method"), msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        return _result(rid, {
            "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return _result(rid, {})
    if method == "tools/list":
        return _result(rid, {"tools": TOOLS})
    if method == "tools/call":
        return _result(rid, _call_tool(params.get("name", ""), params.get("arguments") or {}))

    if rid is None:
        return None
    return _error(rid, -32601, f"Method not found: {method}")


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue

        try:
            reply = handle(msg)
        except Exception as e:  # noqa: BLE001
            _log(traceback.format_exc())
            reply = _error(msg.get("id"), -32603, str(e))

        if reply is not None:
            print(json.dumps(reply, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
