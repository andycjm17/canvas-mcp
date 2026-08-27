"""Canvas LMS MCP server（stdio / JSON-RPC 2.0，纯标准库）。

只读：所有工具都只发 GET，不会替你交作业、改成绩或发帖。

stdout 是协议通道，任何日志一律走 stderr。
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import traceback
from typing import Any, Callable

from . import api, config, model

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
    """把课程名（可模糊）或 id 解析成 (course_id, 课程名)。"""
    if name_or_id is None or name_or_id == "":
        raise ValueError("要指定 course：课程 id 或课程名（可只写一部分）。")

    courses = _active_courses(client)
    text = str(name_or_id).strip()

    if text.isdigit():
        cid = int(text)
        for c in courses:
            if c.get("id") == cid:
                return cid, c.get("name") or str(cid)
        # 不在活跃列表里也放行，可能是已结课的课程
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
        raise ValueError(f"找不到课程 {text!r}。当前活跃课程：{names}")
    if len(matches) > 1:
        names = "、".join(c.get("name", "?") for c in matches)
        raise ValueError(f"课程名 {text!r} 不唯一，匹配到：{names}")
    return matches[0]["id"], matches[0].get("name") or str(matches[0]["id"])


# ---------------------------------------------------------------- 工具实现

def tool_canvas_status() -> Any:
    """连通性自检。出问题先用它。"""
    client = _client()
    me = client.get("/users/self")
    courses = _active_courses(client)
    return {
        "host": client.host,
        "user": me.get("name"),
        "user_id": me.get("id"),
        "timezone": config.timezone_name(),
        "active_courses": len(courses),
        "token_source": ("环境变量 CANVAS_MCP_TOKEN" if "CANVAS_MCP_TOKEN" in os.environ
                         else str(config.CONFIG_PATH)),
        "note": "Canvas 返回的时间都是 UTC，本 server 已按上面的时区转成本地时间。",
    }


def tool_list_courses(include_finished: bool = False) -> Any:
    """列出课程。默认只给还在进行的。"""
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
    """未来 N 天的作业、考试、日历事件和公告，按时间排序。

    走 /planner/items，它把各种类型揉在一个流里，比逐个课程翻作业省事。
    """
    if days < 1 or days > 180:
        raise ValueError("days 取值范围 1-180。")
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
    """还没交、且已经过了截止时间的作业。"""
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
    """某门课的作业列表，含截止时间和你的提交状态。"""
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
    """取一条作业的完整详情，包含要求正文（HTML 会转成纯文本）。"""
    client = _client()
    cid, cname = _resolve_course(client, course)
    raw = client.get(
        f"/courses/{cid}/assignments/{assignment_id}",
        **{"include[]": ["submission"]},
    )
    return model.assignment(raw, course_name=cname, detail=True)


def tool_list_announcements(course: Any = None, days: int = 30, limit: int = 20) -> Any:
    """最近的课程公告。不指定 course 就把所有活跃课程一起查。"""
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
    """各门课的当前总分。只有已经登过分的课才有数。"""
    client = _client()
    # 成绩里会出现已结课的课程，只查活跃列表的话名字会退化成 "course <id>"。
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


# ---------------------------------------------------------------- 工具声明

def _s(**props: Any) -> dict:
    return {"type": "object", "properties": props}


_COURSE = {"type": "string", "description": "课程 id 或课程名（可只写一部分，如 'Data Analytics'）"}

TOOLS: list[dict] = [
    {
        "name": "canvas_status",
        "description": "自检：确认 token 有效、连的是哪个 Canvas 实例、时区设置。排查问题先用它。",
        "inputSchema": _s(),
    },
    {
        "name": "list_courses",
        "description": "列出课程及其学期、当前总分。",
        "inputSchema": _s(include_finished={
            "type": "boolean", "description": "是否带上已结课的，默认否"}),
    },
    {
        "name": "upcoming",
        "description": "未来 N 天要交的作业、考试、课程日历和公告，按时间排序。"
                       "问『这周要交什么』『接下来有啥』用这个。",
        "inputSchema": _s(
            days={"type": "integer", "description": "往后看几天，默认 14"},
            include_done={"type": "boolean", "description": "是否包含已提交的，默认否"},
        ),
    },
    {
        "name": "overdue",
        "description": "已经过了截止时间但还没交的作业。",
        "inputSchema": _s(),
    },
    {
        "name": "list_assignments",
        "description": "某门课的全部作业，含截止时间、分值和你的提交/评分状态。",
        "inputSchema": {
            **_s(
                course=_COURSE,
                only_upcoming={"type": "boolean", "description": "只要还没到期且未提交的，默认否"},
                limit={"type": "integer", "description": "最多返回条数，默认 100"},
            ),
            "required": ["course"],
        },
    },
    {
        "name": "get_assignment",
        "description": "取一条作业的完整要求正文（HTML 已转纯文本）。id 从 list_assignments 拿。",
        "inputSchema": {
            **_s(course=_COURSE, assignment_id={"type": "integer"}),
            "required": ["course", "assignment_id"],
        },
    },
    {
        "name": "list_announcements",
        "description": "最近的课程公告。不指定 course 就查所有活跃课程。",
        "inputSchema": _s(
            course=_COURSE,
            days={"type": "integer", "description": "往前看几天，默认 30"},
            limit={"type": "integer", "description": "最多返回条数，默认 20"},
        ),
    },
    {
        "name": "list_grades",
        "description": "各门课的当前总分。",
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
        return {"content": [{"type": "text", "text": f"未知工具: {name}"}], "isError": True}
    try:
        out = handler(**args)
        text = json.dumps(out, ensure_ascii=False, indent=2)
        return {"content": [{"type": "text", "text": text}]}
    except (api.ApiError, config.ConfigError, ValueError) as e:
        return {"content": [{"type": "text", "text": str(e)}], "isError": True}
    except TypeError as e:
        return {"content": [{"type": "text", "text": f"参数有误: {e}"}], "isError": True}
    except Exception as e:  # noqa: BLE001 - 兜底，避免整个 server 挂掉
        _log(traceback.format_exc())
        return {"content": [{"type": "text", "text": f"内部错误: {e}"}], "isError": True}


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
