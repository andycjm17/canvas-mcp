#!/usr/bin/env python3
"""Twice-daily Canvas digest as a macOS notification.

Designed to run unattended from launchd, so it never raises: a network outage or
an expired token is logged and swallowed rather than surfaced as a crash dialog.
Read the log at ~/.config/canvas-mcp/notify.log when a run seems to have gone
missing.

macOS only — it shells out to osascript. Everything above this file is portable.

    ./notify.py            send the notification
    ./notify.py --dry-run  print what would be sent, send nothing
"""
from __future__ import annotations

import os
import smtplib
import subprocess
import sys
import traceback
from datetime import datetime
from email.message import EmailMessage

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from canvas_mcp import config, i18n, server  # noqa: E402

WINDOW_DAYS = 14
MAX_LINES = 3          # a macOS notification body shows about this many
LOG = config.CONFIG_DIR / "notify.log"


def log(msg: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {msg}\n"
    try:
        config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass
    sys.stderr.write(line)


def _ignored() -> set:
    """Assignment ids to never mention.

    Onboarding checklists you have decided not to do would otherwise lead every
    notification, twice a day, until the term ends. Set "notify_ignore": [id, ...]
    in config.json.
    """
    raw = config._file().get("notify_ignore") or []
    return {str(x) for x in raw}


def collect() -> tuple:
    """Return (assignments due inside the window, overdue assignments).

    Calendar events are dropped — "due" means something you hand in.
    """
    skip = _ignored()
    upcoming = server.tool_upcoming(days=WINDOW_DAYS)
    due = [i for i in upcoming["items"]
           if str(i.get("type", "")).lower() == "assignment"
           and str(i.get("url", "")).rsplit("/", 1)[-1] not in skip]
    overdue = [i for i in server.tool_overdue()["items"]
               if str(i.get("id")) not in skip]
    return due, overdue


def compose(due: list, overdue: list) -> tuple:
    """Build (title, subtitle, body) for the notification."""
    title = i18n.t("notify.title")

    if overdue:
        subtitle = i18n.t("notify.summary", due=len(due), overdue=len(overdue))
    elif due:
        subtitle = i18n.t("notify.due_only", due=len(due), days=WINDOW_DAYS)
    else:
        return title, i18n.t("notify.clear", days=WINDOW_DAYS), ""

    lines = []
    for item in overdue[:MAX_LINES]:
        when = item.get("due") or {}
        lines.append(i18n.t("notify.line", rel=when.get("relative", ""),
                            time=when.get("local", "")[-5:], name=item.get("name", "")))
    for item in due[:MAX_LINES - len(lines)]:
        when = item.get("when") or {}
        lines.append(i18n.t("notify.line", rel=when.get("relative", ""),
                            time=when.get("local", "")[-5:], name=item.get("title", "")))
    return title, subtitle, "\n".join(lines)


def _applescript_str(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def notify(title: str, subtitle: str, body: str) -> None:
    script = (f"display notification {_applescript_str(body)} "
              f"with title {_applescript_str(title)} "
              f"subtitle {_applescript_str(subtitle)}")
    subprocess.run(["osascript", "-e", script], check=True,
                   capture_output=True, timeout=30)


def compose_email(due: list, overdue: list) -> tuple:
    """Build (subject, body). Unlike the notification, this can be complete."""
    if overdue:
        summary = i18n.t("notify.summary", due=len(due), overdue=len(overdue))
    elif due:
        summary = i18n.t("notify.due_only", due=len(due), days=WINDOW_DAYS)
    else:
        summary = i18n.t("notify.clear", days=WINDOW_DAYS)

    # A section header is only worth its space when there are two sections to
    # tell apart; with one, it just restates the summary line above it.
    headed = bool(overdue) and bool(due)

    def block(header: str, items: list, when_key: str, name_key: str) -> list:
        out = [header, "-" * 46, ""] if headed else []
        for item in items:
            when = item.get(when_key) or {}
            # "2026-09-06 23:59" -> "09-06 23:59"; the year is never the question
            stamp = str(when.get("local", ""))[5:]
            out.append(f"  {stamp}  {when.get('weekday','')}"
                       f"   ·   {when.get('relative','')}")
            out.append(f"      {item.get(name_key, '')}")
            if item.get("course"):
                out.append(f"      {item['course']}")
            out.append("")
        return out

    lines = [summary, ""]
    if overdue:
        lines += block(i18n.t("notify.section_overdue"), overdue, "due", "name")
    if due:
        lines += block(i18n.t("notify.section_due"), due, "when", "title")

    return i18n.t("notify.subject"), "\n".join(lines).rstrip() + "\n"


def send_email(subject: str, body: str) -> bool:
    """Send over SMTP. Returns False when no credentials are configured.

    Gmail rejects account passwords over SMTP; this needs an app password
    (Google Account -> Security -> 2-Step Verification -> App passwords).
    Put it in config.json as "smtp_password", alongside "smtp_user".
    """
    cfg = config._file()
    user = os.environ.get("CANVAS_MCP_SMTP_USER") or cfg.get("smtp_user")
    password = os.environ.get("CANVAS_MCP_SMTP_PASSWORD") or cfg.get("smtp_password")
    if not (user and password):
        return False

    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = cfg.get("email_to") or user
    msg["Subject"] = subject
    msg.set_content(body)

    host = cfg.get("smtp_host", "smtp.gmail.com")
    port = int(cfg.get("smtp_port", 465))
    with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
        smtp.login(user, password.replace(" ", ""))  # app passwords are shown spaced
        smtp.send_message(msg)
    return True


def main() -> int:
    dry = "--dry-run" in sys.argv
    try:
        due, overdue = collect()
        title, subtitle, body = compose(due, overdue)
    except Exception as e:  # noqa: BLE001 - unattended: log, never crash
        log(f"FAILED to collect: {e}\n{traceback.format_exc()}")
        return 1

    if dry:
        print(f"--- notification ---\ntitle   : {title}\nsubtitle: {subtitle}\n"
              f"body    :\n{body}")
        subject, mail_body = compose_email(due, overdue)
        print(f"\n--- email ---\nsubject: {subject}\n\n{mail_body}")
        return 0

    try:
        notify(title, subtitle, body)
    except Exception as e:  # noqa: BLE001
        log(f"FAILED to notify: {e}")
        return 1

    # Email is best-effort and must not fail the run: the notification already
    # landed, and a mail outage at 8am should not read as a broken job.
    mailed = "no creds"
    try:
        subject, mail_body = compose_email(due, overdue)
        mailed = "sent" if send_email(subject, mail_body) else "no creds"
    except Exception as e:  # noqa: BLE001
        mailed = f"FAILED ({e})"
        log(f"email failed: {traceback.format_exc()}")

    log(f"notified — {subtitle} | email: {mailed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
