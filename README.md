# canvas-mcp

An MCP server for Canvas LMS. Standard library only, no dependencies, **read-only**.

Built against Fuqua's own Canvas instance, but the host is configurable, so it works
against any Canvas deployment.

## Why

The Canvas web UI takes four or five clicks to answer "what is actually due this week,"
and there is no cross-course view. This server merges assignments, exams, course
calendar entries and announcements into one stream sorted by time.

## Two traps (and the main reason this exists)

**1. Tokens do not cross instances.**
Fuqua runs `fuqua.instructure.com`, **not** `canvas.duke.edu`. The same token against
the Duke host returns `401 Invalid access token.` — which looks like an expired token
but is really a wrong host. This server's 401 message says so directly.

**2. `due_at` is UTC, and reading it as-is puts every deadline a day late.**

```
"due_at": "2026-09-07T03:59:59Z"   ← actually Sun 09-06 23:59 EDT
```

The school sets deadlines at 23:59 local, which rolls past midnight in UTC. Every
timestamp this server emits goes through `model.local()`, which returns the local
time, the weekday, and a relative offset ("10 days out" / "24 days overdue").

## Install

```bash
git clone git@github.com:andycjm17/canvas-mcp.git
cd canvas-mcp
```

No dependencies to install. Python 3.9+ (it uses `zoneinfo`).

## Configure

Generate a token in Canvas under Account → Settings → Approved Integrations →
**+ New Access Token**.

Write it to `~/.config/canvas-mcp/config.json` (forced to mode `0600`):

```bash
python3 -c "
import sys; sys.path.insert(0, '.')
from canvas_mcp import config
config.save('<your token>', host_value='fuqua.instructure.com', timezone_value='America/New_York')
"
```

Environment variables work too and take precedence: `CANVAS_MCP_TOKEN`,
`CANVAS_MCP_HOST`, `CANVAS_MCP_TZ`.

**The token never enters version control.** `.gitignore` already blocks
`config.json`, `*.token` and `.env`.

## Register with Claude Code

```bash
claude mcp add canvas -s user -- python3 /path/to/canvas-mcp/run.py
```

## Tools

| Tool | Purpose |
|---|---|
| `canvas_status` | Self-check: token validity, which instance, timezone. Start here when debugging |
| `list_courses` | Courses with term and current overall score |
| `upcoming` | **The main one.** Assignments, exams, calendar and announcements for the next N days, sorted by time |
| `overdue` | Past-due work that has not been submitted |
| `list_assignments` | One course's assignments, with due dates, point values, submission and grading status |
| `get_assignment` | Full instructions body for a single assignment (HTML converted to plain text) |
| `list_announcements` | Recent announcements, optionally across all courses |
| `list_grades` | Current overall score per course |

Course parameters accept an id or a name, and the name can be partial —
`"Data Analytics"` resolves to `Data Analytics for Business`. An ambiguous name
raises an error listing the candidates rather than guessing.

Tool descriptions and error messages are in Chinese, matching the language the
author works in. Comments and this README are in English.

## Implementation notes

- **Pagination**: Canvas puts the next page in the `Link` response header under
  `rel="next"`, and defaults to `per_page=10`. Without following `next`, anything
  sizeable is silently truncated. `api.paginate()` follows it to the end, capped at
  50 pages to guard against a cyclic chain.
- **Array parameters**: Canvas repeats keys — `state[]=active&state[]=completed` —
  so `_encode()` expands list values into multiple pairs. A plain
  `urlencode(dict)` will not do.
- **`/planner/items`**: the data source for `upcoming`. It merges assignments,
  calendar events, announcements and quizzes into one stream. The one consistent
  timestamp is `plannable_date`; each type's own field is nested under `plannable`
  with a different shape, so always key off `plannable_date`.
- **Grades**: finished courses are absent from `enrollment_state=active`, so the
  name lookup must include `state[]=completed` or entries degrade to
  `"course 3639"`.

## Read-only

Every tool issues GETs only. It will not submit assignments, change grades, post to
discussions, or drop courses.

## Layout

```
run.py                 Entry point (sets sys.path explicitly; MCP clients
                       start the server from an unpredictable directory)
canvas_mcp/config.py   Credentials and instance config, written 0600
canvas_mcp/api.py      HTTP client, pagination, error translation
canvas_mcp/model.py    Timezone conversion, HTML to text, response shaping
canvas_mcp/server.py   Tool definitions + JSON-RPC over stdio
```
