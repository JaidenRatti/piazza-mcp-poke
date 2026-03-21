"""Piazza watcher daemon — pushes notifications to Poke via send_message.

This is a standalone background process that periodically checks Piazza
and sends summaries/alerts to you via Poke's API. Unlike the callback-
powered MCP tools, this runs independently and doesn't require Poke to
initiate a conversation first.

Usage:
    PIAZZA_EMAIL=you@school.ca PIAZZA_PASSWORD=pass \
    POKE_API_KEY=pk_... \
    uv run python -m piazza_mcp.watcher

    # Or with custom schedule:
    WATCH_INTERVAL=300 DIGEST_HOUR=8 uv run python -m piazza_mcp.watcher

Env vars:
    PIAZZA_EMAIL / PIAZZA_PASSWORD  — Piazza credentials
    POKE_API_KEY                    — Poke API key (get from poke.com/kitchen/api-keys)
    WATCH_INTERVAL                  — Seconds between polls for new posts (default: 300)
    DIGEST_HOUR                     — Hour (0-23) to send daily digest (default: 8)
    WATCH_FOLDERS                   — Comma-separated folders to monitor (optional)
"""

import html
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone

from piazza_api import Piazza
from poke import Poke

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("piazza_watcher")

# Deadline keywords (same as server.py)
_DEADLINE_KEYWORDS = re.compile(
    r"\b(due|deadline|extension|submit|submission|marmoset|markus|gradescope"
    r"|late\s*day|penalty|cutoff|closes?|turned?\s*in)\b",
    re.IGNORECASE,
)


def _build_digest(piazza: Piazza, hours: int = 24) -> str | None:
    """Build a digest of activity across all classes in the last N hours."""
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
    status = piazza.get_user_status()
    active = [c for c in status.get("networks", []) if c.get("status") == "active"]

    sections: list[str] = []
    for cls in active:
        name = cls.get("name", "Unknown")
        num = cls.get("course_number", "")
        display = f"{name} ({num})" if num else name
        nid = cls.get("id", "")
        net = piazza.network(nid)

        try:
            feed = net.get_feed(limit=100, offset=0)["feed"]
        except Exception:
            continue

        recent = []
        for p in feed:
            mod = p.get("modified", "")
            if not mod:
                continue
            try:
                post_dt = datetime.fromisoformat(mod.replace("Z", "+00:00"))
                if post_dt >= cutoff:
                    recent.append(p)
            except ValueError:
                continue

        if not recent:
            continue

        notes = [p for p in recent if p.get("type") == "note"]
        instr = [p for p in recent if p.get("has_i")]
        unans = [p for p in recent if p.get("no_answer")]

        lines = [f"{display} — {len(recent)} new post(s)"]
        if notes:
            lines.append(f"  📢 {len(notes)} announcement(s)")
        if instr:
            lines.append(f"  🧑‍🏫 {len(instr)} with instructor replies")
        if unans:
            lines.append(f"  ❓ {len(unans)} unanswered")
        sections.append("\n".join(lines))

    if not sections:
        return None
    return "📋 Daily Piazza Digest\n\n" + "\n\n".join(sections)


def _check_for_alerts(
    piazza: Piazza,
    seen: dict[str, set[int]],
    folders: list[str] | None = None,
) -> list[str]:
    """Check for new instructor posts and deadline alerts. Returns messages."""
    status = piazza.get_user_status()
    active = [c for c in status.get("networks", []) if c.get("status") == "active"]
    alerts: list[str] = []

    for cls in active:
        name = cls.get("name", "Unknown")
        num = cls.get("course_number", "")
        display = f"{name} ({num})" if num else name
        nid = cls.get("id", "")
        net = piazza.network(nid)

        if nid not in seen:
            # First run — seed with current posts
            try:
                feed = net.get_feed(limit=50, offset=0)["feed"]
                seen[nid] = {p.get("nr") for p in feed if p.get("nr")}
            except Exception:
                seen[nid] = set()
            continue

        try:
            feed = net.get_feed(limit=30, offset=0)["feed"]
        except Exception:
            continue

        for post in feed:
            nr = post.get("nr")
            if not nr or nr in seen[nid]:
                continue
            seen[nid].add(nr)

            subj = html.unescape(post.get("subject", "(no subject)"))
            is_note = post.get("type") == "note"
            has_i = post.get("has_i")

            # Alert on instructor posts / announcements
            if is_note:
                alerts.append(f"📢 New announcement in {display}:\n@{nr}: {subj}")
            elif has_i:
                alerts.append(
                    f"🧑‍🏫 New instructor reply in {display}:\n@{nr}: {subj}"
                )

            # Alert on deadline-related posts
            if _DEADLINE_KEYWORDS.search(subj):
                alerts.append(f"⏰ Deadline post in {display}:\n@{nr}: {subj}")

    return alerts


def main() -> None:
    """Run the watcher daemon."""
    email = os.environ.get("PIAZZA_EMAIL")
    password = os.environ.get("PIAZZA_PASSWORD")
    if not email or not password:
        log.error("PIAZZA_EMAIL and PIAZZA_PASSWORD are required")
        return

    poke_key = os.environ.get("POKE_API_KEY")
    if not poke_key:
        log.error("POKE_API_KEY is required (get from poke.com/kitchen/api-keys)")
        return

    interval = int(os.environ.get("WATCH_INTERVAL", "300"))
    digest_hour = int(os.environ.get("DIGEST_HOUR", "8"))
    folders_str = os.environ.get("WATCH_FOLDERS", "")
    folders = [f.strip() for f in folders_str.split(",") if f.strip()] or None

    log.info("Starting Piazza watcher daemon")
    log.info("  Poll interval: %ds", interval)
    log.info("  Daily digest at: %d:00", digest_hour)
    if folders:
        log.info("  Watching folders: %s", ", ".join(folders))

    piazza = Piazza()
    piazza.user_login(email=email, password=password)
    log.info("Logged in to Piazza as %s", email)

    poke_client = Poke(api_key=poke_key)
    log.info("Poke client ready")

    seen: dict[str, set[int]] = {}
    last_digest_date: str | None = None

    while True:
        now = datetime.now(tz=timezone.utc)

        # Daily digest
        today_str = now.strftime("%Y-%m-%d")
        if now.hour == digest_hour and last_digest_date != today_str:
            log.info("Sending daily digest...")
            digest = _build_digest(piazza)
            if digest:
                try:
                    poke_client.send_message(digest)
                    log.info("Daily digest sent")
                except Exception as e:
                    log.error("Failed to send digest: %s", e)
            else:
                log.info("No activity in last 24h — skipping digest")
            last_digest_date = today_str

        # Real-time alerts
        alerts = _check_for_alerts(piazza, seen, folders)
        for alert in alerts:
            log.info("Sending alert: %s", alert[:80])
            try:
                poke_client.send_message(alert)
            except Exception as e:
                log.error("Failed to send alert: %s", e)

        time.sleep(interval)


if __name__ == "__main__":
    main()
