"""English catalogue. This is the fallback: every key must exist here."""

MESSAGES = {
    # ---------------------------------------------------------------- errors
    "err.auth_generic": "authentication failed",
    "err.auth": ("401 {detail}. Check that the token was issued by {host} — "
                 "Canvas tokens are not valid across instances."),
    "err.forbidden": "403 Not permitted to access that resource. {detail}",
    "err.not_found": "404 That resource does not exist, or you lack access. {detail}",
    "err.unreachable": "Cannot reach {host}: {reason}",
    "err.bad_json": "Response was not valid JSON (first 200 chars): {body}",

    "err.config_bad_json": "{path} is not valid JSON: {error}",
    "err.config_not_object": "{path} must hold a JSON object at the top level",
    "err.no_token": (
        "No Canvas access token found. Either:\n"
        "  1. Put it in {path} as {{\"token\": \"<token>\"}} (written with mode 0600), or\n"
        "  2. Set the CANVAS_MCP_TOKEN environment variable.\n"
        "Generate one in Canvas under Account -> Settings -> Approved Integrations "
        "-> + New Access Token."
    ),

    "err.course_required": "A course is required: pass a course id or name (a partial name works).",
    "err.course_not_found": "No course matching {name}. Active courses: {names}",
    "err.course_ambiguous": "Course name {name} is ambiguous; it matched: {names}",
    "err.days_range": "days must be between 1 and 180.",
    "err.unknown_tool": "Unknown tool: {name}",
    "err.bad_args": "Bad arguments: {error}",
    "err.internal": "Internal error: {error}",

    # ---------------------------------------------------------------- time
    "time.weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    "time.today": "today",
    "time.tomorrow": "tomorrow",
    "time.in_days": "in {n} days",
    "time.overdue": "{n} days overdue",
    "text.truncated": "\n… (truncated; original was {n} characters)",

    # ---------------------------------------------------------------- status
    "status.token_env": "CANVAS_MCP_TOKEN environment variable",
    "status.note": ("Canvas returns every timestamp in UTC; this server has already "
                    "converted them to the timezone shown above."),

    # ---------------------------------------------------------------- tools
    "tool.course_param": "Course id, or course name (a partial name works, e.g. 'Data Analytics')",
    "tool.canvas_status": ("Self-check: confirms the token works, which Canvas instance "
                           "it reaches, and the timezone in use. Start here when debugging."),
    "tool.list_courses": "List courses with their term and current overall score.",
    "arg.include_finished": "Include finished courses. Defaults to false.",
    "tool.upcoming": ("Assignments, exams, course calendar entries and announcements due in "
                      "the next N days, sorted by time. Use this for \"what is due this "
                      "week\" or \"what is coming up\"."),
    "arg.days_forward": "How many days ahead to look. Defaults to 14.",
    "arg.include_done": "Include already-submitted items. Defaults to false.",
    "tool.overdue": "Assignments past their due date that have not been submitted.",
    "tool.list_assignments": ("Every assignment in one course, with due dates, point values "
                              "and your submission and grading status."),
    "arg.only_upcoming": "Only items not yet due and not yet submitted. Defaults to false.",
    "arg.limit_100": "Maximum number of items to return. Defaults to 100.",
    "tool.get_assignment": ("Full instructions body for one assignment (HTML converted to "
                            "plain text). Get the id from list_assignments."),
    "tool.list_announcements": ("Recent course announcements. With no course given, queries "
                                "every active course."),
    "arg.days_back": "How many days back to look. Defaults to 30.",
    "arg.limit_20": "Maximum number of items to return. Defaults to 20.",
    "tool.list_grades": "Current overall score for each course.",

    # ---------------------------------------------------------------- notify
    "notify.title": "Canvas",
    "notify.summary": "{due} due · {overdue} overdue",
    "notify.due_only": "{due} due in the next {days} days",
    "notify.clear": "Nothing due in the next {days} days",
    "notify.line": "{rel} {time} · {name}",
}
