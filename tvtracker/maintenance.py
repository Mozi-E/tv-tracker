"""Dated maintenance tasks -> Telegram reminders at 20 / 10 / 1 days before,
plus an overdue notice. Pure logic; the caller does the sending.

A task in data/maintenance.json:
    {
      "id": "cloudflare-pat",
      "title": "Renew the GitHub PAT ...",
      "due": "2026-11-29",          # ISO date
      "notes": "what to actually do",
      "notified": [],               # milestones already sent (managed here)
      "due_seen": "2026-11-29"      # last 'due' we armed for (managed here)
    }
Editing 'due' re-arms every reminder for that task.
"""
from datetime import date

MILESTONES = [20, 10, 1]  # days before the due date


def _next_milestone(days_left: int, notified: list):
    """Return the milestone key to fire now, or None."""
    if days_left <= 0:
        return "overdue" if "overdue" not in notified else None
    for m in sorted(MILESTONES):  # most urgent first
        if days_left <= m and str(m) not in notified:
            return str(m)
    return None


def _describe(days_left: int, due_str: str) -> str:
    if days_left < 0:
        return f"OVERDUE by {-days_left} day(s) (was due {due_str})"
    if days_left == 0:
        return f"due today ({due_str})"
    if days_left == 1:
        return f"due tomorrow ({due_str})"
    return f"in {days_left} days ({due_str})"


def scan(tasks: list, today: date = None):
    """Re-arm tasks whose 'due' changed, then return [(task, milestone, text)]
    for every reminder that should be sent on this run."""
    today = today or date.today()
    pending = []
    for t in tasks:
        due_str = t.get("due")
        try:
            due = date.fromisoformat(due_str)
        except (TypeError, ValueError):
            continue
        if t.get("due_seen") != due_str:  # date edited -> start over
            t["notified"] = []
            t["due_seen"] = due_str
        notified = t.setdefault("notified", [])
        days_left = (due - today).days
        milestone = _next_milestone(days_left, notified)
        if milestone is None:
            continue
        title = t.get("title") or t.get("id") or "maintenance task"
        text = f"Maintenance reminder - {_describe(days_left, due_str)}:\n{title}"
        if t.get("notes"):
            text += f"\n\n{t['notes']}"
        pending.append((t, milestone, text))
    return pending


def mark(task: dict, milestone: str) -> None:
    """Record a milestone as sent (and suppress any larger ones already passed)."""
    notified = task.setdefault("notified", [])
    if milestone == "overdue":
        if "overdue" not in notified:
            notified.append("overdue")
        return
    m = int(milestone)
    for mm in MILESTONES:
        if mm >= m and str(mm) not in notified:
            notified.append(str(mm))
