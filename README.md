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

The fastest path is to hand this repository's URL to your coding agent and ask it
to set the server up. Everything below is written so an agent can follow it
unaided — clone, config file, registration command.

**The one step an agent cannot do for you is get the token.** That needs your
Canvas login and whatever MFA your school enforces, so generate it yourself (next
section) and hand it over. Everything else is mechanical.

Doing it by hand instead:

```bash
git clone https://github.com/andycjm17/canvas-mcp.git
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
`CANVAS_MCP_HOST`, `CANVAS_MCP_TZ`, `CANVAS_MCP_LANG`.

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

## Language

Everything a user reads — tool descriptions, error messages, relative-time labels
like "in 10 days" — comes from a catalogue in `canvas_mcp/i18n/`, not from strings
inlined in the logic. English (`en.py`) is the default and the only catalogue
shipped here.

To add another language, drop in `canvas_mcp/i18n/<code>.py` exporting a
`MESSAGES` dict and select it with `"lang": "<code>"` in config.json, or
`CANVAS_MCP_LANG`:

```python
# canvas_mcp/i18n/de.py
MESSAGES = {
    "time.today": "heute",
    "err.days_range": "days muss zwischen 1 und 180 liegen.",
    # ... any key you leave out falls back to English
}
```

Missing keys, an unknown language code, and a catalogue that fails to import all
fall back to English rather than raising, so a partial translation is safe to
ship and can never take the server down.

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
- **Catalogue loading**: `i18n` imports `config` lazily, inside the lookup rather
  than at module level, because `config` needs `i18n` for its own error strings.
  A top-level import in either direction deadlocks on the circular reference.

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
canvas_mcp/i18n/       Message catalogues; en.py is the default and the fallback
```

## License

MIT — see [LICENSE](LICENSE). Use it, fork it, ship it.

Not affiliated with or endorsed by Instructure. "Canvas" is their trademark.
