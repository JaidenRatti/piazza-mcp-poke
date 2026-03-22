import asyncio
import html
import os
import re
from datetime import datetime, timedelta, timezone

import uvicorn
from fastmcp import FastMCP
from piazza_api import Piazza
from piazza_api.network import (
    FolderFilter,
    FollowingFilter,
    Network,
    UnreadFilter,
)
from poke.mcp import PokeCallbackMiddleware, with_callbacks

from piazza_mcp.formatting import (
    format_full_post,
    html_to_markdown,
    make_snippet,
)

mcp = FastMCP("piazza")

# Global state
_piazza: Piazza | None = None
_network: Network | None = None


def _login() -> Piazza:
    """Authenticate with Piazza using environment variables."""
    global _piazza
    if _piazza is not None:
        return _piazza
    email = os.environ.get("PIAZZA_EMAIL")
    password = os.environ.get("PIAZZA_PASSWORD")
    if not email or not password:
        raise RuntimeError(
            "PIAZZA_EMAIL and PIAZZA_PASSWORD environment variables are required"
        )
    p = Piazza()
    p.user_login(email=email, password=password)
    _piazza = p
    return p


def _get_network() -> Network:
    """Return the active Network, or raise if none is set."""
    if _network is None:
        raise RuntimeError("No class selected. Call set_class(network_id) first.")
    return _network


def _get_all_networks() -> list[tuple[str, Network]]:
    """Return Network objects for all active classes.

    Returns a list of (class_display_name, Network) tuples.
    """
    p = _login()
    status = p.get_user_status()
    active = [c for c in status.get("networks", []) if c.get("status") == "active"]
    results = []
    for c in active:
        name = c.get("name", "Unknown")
        term = c.get("term", "")
        num = c.get("course_number", "")
        display = name
        if num:
            display += f" ({num})"
        if term:
            display += f" — {term}"
        nid = c.get("id", "")
        results.append((display, p.network(nid)))
    return results


@mcp.tool()
def list_classes() -> str:
    """Call this first to see your enrolled Piazza classes. Only active classes
    are shown. You must then call set_class with the appropriate network_id
    before you can search or read posts. Use context clues (project directory,
    what the user is asking about, class name/number) to determine the right
    class. If it's obvious from context, proceed. If ambiguous, ask the user
    which class they mean."""
    p = _login()
    status = p.get_user_status()
    raw_classes = status.get("networks", [])
    if not raw_classes:
        return "No enrolled classes found."
    # Filter to only active classes
    active = [c for c in raw_classes if c.get("status") == "active"]
    if not active:
        return "No active classes found."
    lines = []
    for c in active:
        name = c.get("name", "Unknown")
        term = c.get("term", "")
        num = c.get("course_number", "")
        nid = c.get("id", "")
        line = f"- **{name}**"
        if num:
            line += f" ({num})"
        if term:
            line += f" — {term}"
        line += f"\n  network_id: `{nid}`"
        lines.append(line)
    return "\n".join(lines)


@mcp.tool()
def set_class(network_id: str) -> str:
    """Select a class to work with. Must be called once before searching or
    reading posts. Returns the list of available folders — check these carefully
    since folder names may not match what the user calls things (e.g.,
    'assignment 1' might be folder 'hw1')."""
    global _network

    p = _login()

    # Get class name and folders from user.status — each network object has
    # "folders" directly (the feed endpoint doesn't reliably include them)
    status = p.get_user_status()
    networks = status["networks"]
    matched = [c for c in networks if c["id"] == network_id]
    if not matched:
        raise RuntimeError(f"network_id '{network_id}' not found in enrolled classes")

    _network = p.network(network_id)
    class_info = matched[0]
    name = class_info.get("name", "")
    term = class_info.get("term", "")
    class_name = f"{name} — {term}" if term else name
    folders = class_info.get("folders", [])

    lines = [f"Active class: **{class_name}**", "", "Available folders:"]
    for f in folders:
        lines.append(f"- {f}")
    if not folders:
        lines.append("(no folders found)")
    return "\n".join(lines)


@mcp.tool()
def search_posts(
    query: str | None = None,
    folder: str | None = None,
    limit: int = 20,
) -> str:
    """Search for posts by keyword, filter by folder, or both. Call with no
    arguments to browse recent posts. Use folder names from the set_class
    response. Prefer folder filtering when looking for assignment-specific
    content since keyword search doesn't search folder names. Combine folder +
    query to narrow within a topic.

    IMPORTANT: Keyword search requires ALL keywords to appear in a result —
    if any keyword is missing, the post won't match. Keep queries to 1-2 words
    max. Use the most specific single keyword likely to appear verbatim in
    posts. Run multiple short searches rather than one long query."""
    network = _get_network()

    # search_feed returns a plain list of post dicts.
    # get_feed and get_filtered_feed return {"feed": [...]}.
    if query and folder:
        results = network.search_feed(query)
        posts = [p for p in results if folder in p.get("folders", [])][:limit]
    elif query:
        posts = network.search_feed(query)[:limit]
    elif folder:
        posts = network.get_filtered_feed(FolderFilter(folder))["feed"][:limit]
    else:
        posts = network.get_feed(limit=limit, offset=0)["feed"][:limit]

    if not posts:
        return "No posts found."

    lines = [f"Found {len(posts)} post(s):", ""]
    for post_summary in posts:
        nr = post_summary.get("nr", post_summary.get("id", "?"))
        subject = html.unescape(post_summary.get("subject", "(no subject)"))
        snippet = make_snippet(post_summary.get("content_snipet", ""))
        folders_list = ", ".join(post_summary.get("folders", []))
        modified = post_summary.get("modified", "")
        post_type = post_summary.get("type", "")
        has_i = post_summary.get("has_i")
        has_s = post_summary.get("has_s")
        no_answer = post_summary.get("no_answer")

        line = f"### @{nr}: {subject}"
        if snippet:
            line += f"\n{snippet}"
        meta = []
        if folders_list:
            meta.append(f"Folders: {folders_list}")
        if has_i:
            meta.append("Has instructor answer")
        if has_s:
            meta.append("Has student answer")
        if no_answer:
            meta.append("Unanswered")
        if post_type:
            meta.append(f"Type: {post_type}")
        if modified:
            meta.append(f"Date: {modified}")
        if meta:
            line += "\n" + " | ".join(meta)
        lines.append(line)

    return "\n\n".join(lines)


@mcp.tool()
def get_post(post_number: int) -> str:
    """Get the full content of a specific post including all answers and
    follow-up discussions. Use the post number from search results or from a
    user reference like '@142'."""
    network = _get_network()
    post = network.get_post(post_number)
    return format_full_post(post)


# ---------------------------------------------------------------------------
# New tools for Poke
# ---------------------------------------------------------------------------


def _format_feed_post(post_summary: dict) -> str:
    """Format a single feed-level post summary into readable text."""
    nr = post_summary.get("nr", post_summary.get("id", "?"))
    subject = html.unescape(post_summary.get("subject", "(no subject)"))
    snippet = make_snippet(post_summary.get("content_snipet", ""))
    folders_list = ", ".join(post_summary.get("folders", []))
    modified = post_summary.get("modified", "")
    has_i = post_summary.get("has_i")
    has_s = post_summary.get("has_s")
    no_answer = post_summary.get("no_answer")
    num_followups = post_summary.get("num_followups", 0)

    line = f"### @{nr}: {subject}"
    if snippet:
        line += f"\n{snippet}"
    meta = []
    if folders_list:
        meta.append(f"Folders: {folders_list}")
    if has_i:
        meta.append("Instructor answered")
    if has_s:
        meta.append("Student answered")
    if no_answer:
        meta.append("Unanswered")
    if num_followups:
        meta.append(f"{num_followups} follow-up(s)")
    if modified:
        meta.append(f"Date: {modified}")
    if meta:
        line += "\n" + " | ".join(meta)
    return line


def _get_feed(folder: str | None, limit: int) -> list[dict]:
    """Fetch feed posts, optionally filtered by folder."""
    network = _get_network()
    if folder:
        return network.get_filtered_feed(FolderFilter(folder))["feed"][:limit]
    return network.get_feed(limit=limit, offset=0)["feed"][:limit]


@mcp.tool()
def get_folder_activity(
    folder: str | None = None,
    since: str | None = None,
    limit: int = 20,
) -> str:
    """Get the most recently active posts, optionally filtered to a folder
    and/or a date cutoff. Posts are sorted by last-modified so you see the
    latest discussion first.

    Use this for questions like 'what's going on with assignment 3?',
    'what are people talking about in my distributed systems class?', or
    'what's new since Monday?'.

    The optional `since` parameter filters to posts updated after a date.
    Convert relative dates to ISO format (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)
    before calling."""
    cutoff = None
    if since:
        try:
            if "T" in since:
                cutoff = datetime.fromisoformat(since)
            else:
                cutoff = datetime.strptime(since, "%Y-%m-%d")
        except ValueError:
            return (
                f"Could not parse date '{since}'. "
                f"Use YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS."
            )
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=timezone.utc)

    raw = _get_feed(folder, limit=200 if cutoff else limit)

    if cutoff:
        posts = []
        for p in raw:
            mod = p.get("modified", "")
            if not mod:
                continue
            try:
                post_dt = datetime.fromisoformat(
                    mod.replace("Z", "+00:00")
                )
                if post_dt >= cutoff:
                    posts.append(p)
            except ValueError:
                continue
        posts = posts[:limit]
    else:
        posts = raw

    if not posts:
        since_msg = f" since {since}" if since else ""
        return f"No posts found{since_msg}."

    since_msg = f" since {since}" if since else ""
    lines = [f"Found {len(posts)} recent post(s){since_msg}:", ""]
    for p in posts:
        lines.append(_format_feed_post(p))
    return "\n\n".join(lines)


@mcp.tool()
def get_hot_posts(
    folder: str | None = None,
    limit: int = 10,
) -> str:
    """Get the most-discussed posts — sorted by number of follow-ups.
    These are the posts everyone is asking about. Use this for questions like
    'what are the common issues with the assignment?' or 'what should I know
    before I start?'."""
    # Fetch a larger window so we can rank
    raw = _get_feed(folder, limit=100)
    ranked = sorted(raw, key=lambda p: p.get("num_followups", 0), reverse=True)
    top = ranked[:limit]

    if not top:
        return "No posts found."

    lines = [f"Top {len(top)} most-discussed post(s):", ""]
    for p in top:
        lines.append(_format_feed_post(p))
    return "\n\n".join(lines)


@mcp.tool()
def get_unanswered(
    folder: str | None = None,
    limit: int = 15,
) -> str:
    """Get posts with no answers from anyone (instructor or student).
    Use this for questions like 'what's still unanswered?' or 'are there
    questions nobody has helped with yet?'."""
    raw = _get_feed(folder, limit=100)
    unanswered = [p for p in raw if p.get("no_answer")][:limit]

    if not unanswered:
        return "No unanswered posts found — everything has a response!"

    lines = [f"Found {len(unanswered)} unanswered post(s):", ""]
    for p in unanswered:
        lines.append(_format_feed_post(p))
    return "\n\n".join(lines)


@mcp.tool()
def get_announcements(
    folder: str | None = None,
    limit: int = 10,
) -> str:
    """Get instructor notes and announcements (post type 'note'). These
    typically contain deadlines, clarifications, extensions, and logistics.
    Use this for questions like 'any announcements?' or 'did the prof post
    anything about the deadline?'."""
    raw = _get_feed(folder, limit=100)
    notes = [p for p in raw if p.get("type") == "note"][:limit]

    if not notes:
        return "No announcements found."

    lines = [f"Found {len(notes)} announcement(s):", ""]
    for p in notes:
        lines.append(_format_feed_post(p))
    return "\n\n".join(lines)


@mcp.tool()
def get_instructor_replies(
    folder: str | None = None,
    limit: int = 15,
) -> str:
    """Get recent posts where the instructor has replied. Use this for
    questions like 'what has the prof said about the assignment?' or
    'any instructor clarifications I should know about?'."""
    raw = _get_feed(folder, limit=100)
    with_instructor = [p for p in raw if p.get("has_i")][:limit]

    if not with_instructor:
        return "No posts with instructor replies found."

    lines = [f"Found {len(with_instructor)} post(s) with instructor replies:", ""]
    for p in with_instructor:
        lines.append(_format_feed_post(p))
    return "\n\n".join(lines)


@mcp.tool()
def get_class_stats() -> str:
    """Get statistics and overview for the current class — total posts,
    response rates, active users, etc. Use this for questions like
    'how active is my class?' or 'what are the class stats?'."""
    network = _get_network()
    stats = network.get_statistics()

    # Also compute quick summary from recent feed
    feed = network.get_feed(limit=100, offset=0)["feed"]
    total_in_feed = len(feed)
    with_instructor = sum(1 for p in feed if p.get("has_i"))
    unanswered = sum(1 for p in feed if p.get("no_answer"))
    notes = sum(1 for p in feed if p.get("type") == "note")

    lines = ["**Class Overview** (last 100 posts):"]
    lines.append(f"- Total posts in feed: {total_in_feed}")
    lines.append(f"- With instructor answer: {with_instructor}")
    lines.append(f"- Unanswered: {unanswered}")
    lines.append(f"- Announcements/notes: {notes}")
    if total_in_feed > 0:
        response_rate = (
            (total_in_feed - unanswered) / total_in_feed * 100
        )
        lines.append(f"- Response rate: {response_rate:.0f}%")

    # Include Piazza's own stats if available
    if isinstance(stats, dict):
        total = stats.get("total", {})
        if total.get("questions"):
            lines.append("\n**All-time stats:**")
            lines.append(
                f"- Total questions: {total.get('questions', '?')}"
            )
            lines.append(
                f"- Total posts: {total.get('posts', '?')}"
            )
            days = stats.get("days_since_launch")
            if days:
                lines.append(f"- Days since launch: {days}")

    return "\n".join(lines)


@mcp.tool()
def get_my_posts(
    limit: int = 20,
) -> str:
    """Get posts you are following — these are posts you created, answered,
    or explicitly followed. Use this for questions like 'what posts am I
    tracking?', 'any updates on my questions?', or 'show me stuff I care
    about'."""
    network = _get_network()
    feed = network.get_filtered_feed(FollowingFilter())["feed"][:limit]

    if not feed:
        return "You're not following any posts."

    lines = [f"Found {len(feed)} post(s) you're following:", ""]
    for p in feed:
        lines.append(_format_feed_post(p))
    return "\n\n".join(lines)


@mcp.tool()
def get_unread_posts(
    limit: int = 20,
) -> str:
    """Get posts with unread updates. Use this for questions like 'what's new
    since I last checked?' or 'any unread posts?'."""
    network = _get_network()
    feed = network.get_filtered_feed(UnreadFilter())["feed"][:limit]

    if not feed:
        return "No unread posts — you're all caught up!"

    lines = [f"Found {len(feed)} unread post(s):", ""]
    for p in feed:
        lines.append(_format_feed_post(p))
    return "\n\n".join(lines)


@mcp.tool()
def get_pinned_posts(
    limit: int = 10,
) -> str:
    """Get pinned posts for the current class. Pinned posts usually contain
    important info like due dates, syllabus, office hours, exam logistics, or
    grading policies. Use this for questions like 'what are the pinned posts?',
    'when is the assignment due?', or 'what are the important posts?'."""
    raw = _get_feed(folder=None, limit=200)
    pinned = [p for p in raw if p.get("pin")][:limit]

    if not pinned:
        return "No pinned posts found."

    lines = [f"Found {len(pinned)} pinned post(s):", ""]
    for p in pinned:
        lines.append(_format_feed_post(p))
    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# Deadline extraction
# ---------------------------------------------------------------------------

# Patterns that signal deadline-relevant content
_DEADLINE_KEYWORDS = re.compile(
    r"\b(due|deadline|extension|submit|submission|marmoset|markus|gradescope"
    r"|late\s*day|penalty|cutoff|closes?|turned?\s*in)\b",
    re.IGNORECASE,
)

# Common date patterns: "March 15", "Mar 15", "3/15", "2025-03-15", etc.
_DATE_PATTERN = re.compile(
    r"\b(?:"
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?"
    r"|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?"
    r"|Dec(?:ember)?)\s+\d{1,2}(?:,?\s*\d{4})?"
    r"|\d{1,2}/\d{1,2}(?:/\d{2,4})?"
    r"|\d{4}-\d{2}-\d{2}"
    r")\b",
    re.IGNORECASE,
)


def _extract_deadline_lines(text: str) -> list[str]:
    """Pull sentences/lines that mention deadlines or dates."""
    hits: list[str] = []
    for line in re.split(r"[\n.;]", text):
        line = line.strip()
        if not line:
            continue
        if _DEADLINE_KEYWORDS.search(line) or _DATE_PATTERN.search(line):
            hits.append(line)
    return hits


@mcp.tool()
def get_deadlines(
    folder: str | None = None,
    limit: int = 30,
) -> str:
    """Scan recent posts for deadlines, due dates, extensions, and submission
    info. Parses post content for date patterns and keywords like 'due',
    'extension', 'marmoset', 'gradescope', etc. Use this instead of reading
    50 posts when you just need to know when something is due."""
    network = _get_network()
    feed = _get_feed(folder, limit=limit)

    results: list[str] = []
    for post_summary in feed:
        nr = post_summary.get("nr", post_summary.get("id", "?"))
        subject = html.unescape(post_summary.get("subject", ""))

        # Check subject first (cheap)
        subject_hits = _extract_deadline_lines(subject)

        # Fetch full post to scan content + answers
        try:
            full = network.get_post(nr)
        except Exception:
            if subject_hits:
                results.append(
                    f"- **@{nr}**: {subject}\n"
                    + "\n".join(f"  - {h}" for h in subject_hits)
                )
            continue

        # Collect all text: question body + answers + follow-ups
        all_text = subject + "\n"
        history = full.get("history", [])
        if history:
            all_text += html_to_markdown(history[0].get("content", "")) + "\n"
        for child in full.get("children", []):
            child_hist = child.get("history", [])
            if child_hist:
                all_text += html_to_markdown(
                    child_hist[0].get("content", "")
                ) + "\n"
            all_text += html_to_markdown(child.get("subject", "")) + "\n"

        hits = _extract_deadline_lines(all_text)
        if hits:
            # Deduplicate while preserving order
            seen: set[str] = set()
            unique: list[str] = []
            for h in hits:
                normed = h.lower().strip()
                if normed not in seen:
                    seen.add(normed)
                    unique.append(h)
            results.append(
                f"- **@{nr}: {subject}**\n"
                + "\n".join(f"  - {h}" for h in unique[:5])
            )

    if not results:
        folder_msg = f" in folder '{folder}'" if folder else ""
        return f"No deadline-related posts found{folder_msg}."

    lines = [f"Found deadline info in {len(results)} post(s):", ""]
    lines.extend(results)
    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# Summarize folder chaos
# ---------------------------------------------------------------------------


@mcp.tool()
def summarize_folder_activity(
    folder: str,
    hours: int = 24,
    limit: int = 20,
) -> str:
    """Get a bulleted summary of bugs, clarifications, and key info from a
    folder in the last N hours. Fetches full post content (not just snippets)
    and extracts the important bits. Use this for questions like 'summarize
    the chaos on project 2' or 'what do I need to know about a]3 before I
    start?'. The folder parameter is required."""
    network = _get_network()
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=hours)

    feed = network.get_filtered_feed(FolderFilter(folder))["feed"]

    # Filter to recent posts
    recent: list[dict] = []
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

    recent = recent[:limit]

    if not recent:
        return f"No posts in folder '{folder}' in the last {hours} hours."

    bullets: list[str] = []
    for post_summary in recent:
        nr = post_summary.get("nr", post_summary.get("id", "?"))
        subject = html.unescape(post_summary.get("subject", "(no subject)"))
        has_i = post_summary.get("has_i")
        has_s = post_summary.get("has_s")
        no_answer = post_summary.get("no_answer")

        # Fetch full post for content
        try:
            full = network.get_post(nr)
        except Exception:
            bullets.append(f"- **@{nr}: {subject}** (could not fetch details)")
            continue

        # Get question body
        history = full.get("history", [])
        question_body = ""
        if history:
            question_body = html_to_markdown(history[0].get("content", ""))
        question_snippet = make_snippet(question_body, max_length=200)

        # Get answer summaries
        answer_parts: list[str] = []
        for child in full.get("children", []):
            ctype = child.get("type", "")
            child_hist = child.get("history", [])
            if ctype == "i_answer" and child_hist:
                ans = make_snippet(
                    html_to_markdown(child_hist[0].get("content", "")),
                    max_length=200,
                )
                if ans:
                    answer_parts.append(f"  - **Instructor:** {ans}")
            elif ctype == "s_answer" and child_hist:
                ans = make_snippet(
                    html_to_markdown(child_hist[0].get("content", "")),
                    max_length=200,
                )
                if ans:
                    answer_parts.append(f"  - **Student:** {ans}")

        status_tag = ""
        if has_i:
            status_tag = " [instructor answered]"
        elif has_s:
            status_tag = " [student answered]"
        elif no_answer:
            status_tag = " [unanswered]"

        bullet = f"- **@{nr}: {subject}**{status_tag}"
        if question_snippet:
            bullet += f"\n  - Q: {question_snippet}"
        if answer_parts:
            bullet += "\n" + "\n".join(answer_parts)
        bullets.append(bullet)

    header = (
        f"Summary of **{folder}** — {len(bullets)} post(s) "
        f"in the last {hours}h:\n"
    )
    return header + "\n\n".join(bullets)


# ---------------------------------------------------------------------------
# Unread posts I'm following
# ---------------------------------------------------------------------------


@mcp.tool()
def get_my_unread(
    limit: int = 20,
) -> str:
    """Get unread posts that you are following — threads you created, answered,
    or bookmarked that have new activity. Filters out noise from other people's
    random questions. Use this for 'any updates on my stuff?' or 'what did I
    miss on threads I care about?'."""
    network = _get_network()

    # Get both sets and intersect by post number
    following = network.get_filtered_feed(FollowingFilter())["feed"]
    unread = network.get_filtered_feed(UnreadFilter())["feed"]

    unread_nrs = {p.get("nr") for p in unread if p.get("nr")}
    my_unread = [p for p in following if p.get("nr") in unread_nrs][:limit]

    if not my_unread:
        return "No unread updates on posts you're following — you're caught up!"

    lines = [f"Found {len(my_unread)} unread post(s) you're following:", ""]
    for p in my_unread:
        lines.append(_format_feed_post(p))
    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# Write tools
# ---------------------------------------------------------------------------


@mcp.tool()
def write_post(
    subject: str,
    content: str,
    folder: str,
    anonymous: bool = True,
    private: bool = False,
) -> str:
    """Post a new question to the current class.

    Parameters:
    - folder: must match one of the folders from set_class (e.g. 'hw1')
    - anonymous: if True (default), post as anonymous to other students.
      Note: instructors can always see who posted.
    - private: if True, only visible to you and instructors (for grades,
      extensions, accommodations). Default is False (public).

    IMPORTANT: Before calling this tool, you MUST tell the user exactly
    how the post will appear:
    - Whether it will be PUBLIC or PRIVATE (instructors only)
    - Whether their name will be ANONYMOUS or VISIBLE
    - The subject, content, and folder
    Only call this after the user confirms these settings."""
    network = _get_network()

    params = {
        "anonymous": "stud" if anonymous else "no",
        "subject": subject,
        "content": content,
        "folders": [folder],
        "type": "question",
        "config": {
            "bypass_email": 0,
            "is_announcement": 0,
        },
    }

    if private:
        user_profile = network._rpc.get_user_profile() or {}
        user_id = user_profile.get("user_id")
        if not user_id:
            return (
                "\u274c Could not make post private — failed to "
                "retrieve your user ID. Post was NOT created."
            )
        params["config"]["feed_groups"] = (
            f"instr_{network._nid},{user_id}"
        )

    result = network._rpc.content_create(params)
    nr = result.get("nr", "?")
    visibility = "private (instructors only)" if private else "public"
    identity = "anonymous" if anonymous else "with your name"
    return (
        f"\u2705 Posted {visibility} question {identity}: "
        f"@{nr}: **{subject}** in folder '{folder}'."
    )


@mcp.tool()
def write_reply(
    post_number: int,
    content: str,
    anonymous: bool = True,
) -> str:
    """Add a follow-up reply to an existing post.

    Parameters:
    - post_number: the post to reply to (e.g. 142 for @142)
    - content: the reply text
    - anonymous: if True (default), reply anonymously to other students.
      Note: instructors can always see who replied.

    IMPORTANT: Before calling this tool, tell the user whether the reply
    will be ANONYMOUS or show THEIR NAME, and the content. Only call this
    after the user confirms."""
    network = _get_network()
    post = network.get_post(post_number)
    cid = post.get("id", post_number)

    params = {
        "cid": cid,
        "type": "followup",
        "subject": content,
        "content": "",
        "config": {"editor": "rte"},
        "anonymous": "stud" if anonymous else "no",
    }
    network._rpc.content_create(params)

    identity = "anonymously" if anonymous else "with your name"
    return f"\u2705 Replied to @{post_number} {identity}."


# ---------------------------------------------------------------------------
# Global search (no set_class needed)
# ---------------------------------------------------------------------------


@mcp.tool()
def global_search(
    query: str,
    limit_per_class: int = 10,
) -> str:
    """Search across ALL active classes at once — no need to call set_class
    first. Use this for broad questions like 'any updates on my midterms?'
    or 'anything about extensions?' when you don't know which class to check.
    Results are grouped by class."""
    all_nets = _get_all_networks()
    if not all_nets:
        return "No active classes found."

    sections: list[str] = []
    total = 0
    for class_name, net in all_nets:
        try:
            results = net.search_feed(query)[:limit_per_class]
        except Exception:
            continue
        if not results:
            continue
        total += len(results)
        lines = [f"## {class_name}", ""]
        for post_summary in results:
            nr = post_summary.get("nr", post_summary.get("id", "?"))
            subject = html.unescape(
                post_summary.get("subject", "(no subject)")
            )
            snippet = make_snippet(post_summary.get("content_snipet", ""))
            has_i = post_summary.get("has_i")
            modified = post_summary.get("modified", "")

            line = f"- **@{nr}: {subject}**"
            if snippet:
                line += f" — {snippet}"
            meta = []
            if has_i:
                meta.append("instructor answered")
            if modified:
                meta.append(modified)
            if meta:
                line += f" ({', '.join(meta)})"
            lines.append(line)
        sections.append("\n".join(lines))

    if not sections:
        return f"No results for '{query}' across any class."

    header = (
        f"Found {total} result(s) for '{query}' "
        f"across {len(sections)} class(es):\n"
    )
    return header + "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Proactive / callback-powered tools
# ---------------------------------------------------------------------------


@mcp.tool()
@with_callbacks
async def daily_digest():
    """Generate a daily digest of new activity across ALL enrolled classes.
    Summarizes new posts, instructor announcements, and unanswered questions
    from the last 24 hours. Designed to be triggered by a Poke Kitchen
    automation (e.g. daily at 8am) or called manually.

    When triggered via Poke with callback headers, each class summary is
    streamed back as a separate update so you get incremental results."""
    _login()
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=24)
    all_nets = _get_all_networks()

    if not all_nets:
        yield "No active classes found."
        return

    yield f"Scanning {len(all_nets)} class(es) for activity in the last 24h..."

    for class_name, net in all_nets:
        try:
            feed = net.get_feed(limit=100, offset=0)["feed"]
        except Exception:
            yield f"**{class_name}**: could not fetch feed."
            continue

        recent = []
        for p in feed:
            mod = p.get("modified", "")
            if not mod:
                continue
            try:
                post_dt = datetime.fromisoformat(
                    mod.replace("Z", "+00:00")
                )
                if post_dt >= cutoff:
                    recent.append(p)
            except ValueError:
                continue

        if not recent:
            continue

        instructor_posts = [p for p in recent if p.get("has_i")]
        unanswered = [p for p in recent if p.get("no_answer")]
        notes = [p for p in recent if p.get("type") == "note"]

        lines = [f"**{class_name}** — {len(recent)} new post(s):"]
        if notes:
            lines.append(
                f"  \U0001f4e2 {len(notes)} announcement(s): "
                + ", ".join(
                    f"@{p.get('nr', '?')}" for p in notes[:5]
                )
            )
        if instructor_posts:
            lines.append(
                f"  \U0001f9d1\u200d\U0001f3eb {len(instructor_posts)} "
                f"instructor reply/replies"
            )
        if unanswered:
            lines.append(
                f"  \u2753 {len(unanswered)} unanswered question(s)"
            )

        # Top 3 most-discussed
        by_followups = sorted(
            recent,
            key=lambda x: x.get("num_followups", 0),
            reverse=True,
        )[:3]
        if by_followups:
            lines.append("  Hot threads:")
            for p in by_followups:
                nr = p.get("nr", "?")
                subj = html.unescape(
                    p.get("subject", "(no subject)")
                )
                nf = p.get("num_followups", 0)
                lines.append(f"    - @{nr}: {subj} ({nf} follow-ups)")

        yield "\n".join(lines)

    yield "\u2705 Daily digest complete."


@mcp.tool()
@with_callbacks
async def watch_class(
    network_id: str,
    interval_minutes: int = 5,
    duration_minutes: int = 60,
):
    """Watch a class for new instructor posts and announcements, sending
    real-time updates back to Poke as they appear.

    Polls every `interval_minutes` for up to `duration_minutes`. Each time
    a new instructor post or announcement is detected, it's sent back as a
    callback message.

    Call list_classes first to get the network_id. This tool is designed
    for 'let me know when the prof posts something'."""
    p = _login()
    net = p.network(network_id)

    yield (
        f"Watching for instructor posts every {interval_minutes}m "
        f"for the next {duration_minutes}m..."
    )

    seen_nrs: set[int] = set()
    # Seed with current posts so we only alert on NEW ones
    try:
        initial = net.get_feed(limit=50, offset=0)["feed"]
        for post in initial:
            nr = post.get("nr")
            if nr:
                seen_nrs.add(nr)
    except Exception:
        pass

    end_time = datetime.now(tz=timezone.utc) + timedelta(
        minutes=duration_minutes
    )

    while datetime.now(tz=timezone.utc) < end_time:
        await asyncio.sleep(interval_minutes * 60)

        try:
            feed = net.get_feed(limit=30, offset=0)["feed"]
        except Exception:
            continue

        for post in feed:
            nr = post.get("nr")
            if not nr or nr in seen_nrs:
                continue
            seen_nrs.add(nr)

            has_i = post.get("has_i")
            is_note = post.get("type") == "note"

            if has_i or is_note:
                subj = html.unescape(
                    post.get("subject", "(no subject)")
                )
                tag = "\U0001f4e2 Announcement" if is_note else (
                    "\U0001f9d1\u200d\U0001f3eb Instructor post"
                )
                yield f"{tag}: **@{nr}: {subj}**"

    yield "\u23f0 Watch period ended."


@mcp.tool()
@with_callbacks
async def watch_deadlines(
    network_id: str,
    folder: str | None = None,
    interval_minutes: int = 30,
    duration_minutes: int = 480,
):
    """Monitor for new deadline-related posts (due dates, extensions,
    submission info) and alert you when they appear.

    Polls every `interval_minutes` for up to `duration_minutes` (default 8h).
    Great for 'let me know if there are any deadline changes'."""
    p = _login()
    net = p.network(network_id)

    yield (
        f"Monitoring for deadline updates every {interval_minutes}m "
        f"for {duration_minutes}m..."
    )

    seen_nrs: set[int] = set()
    try:
        initial = net.get_feed(limit=50, offset=0)["feed"]
        for post in initial:
            nr = post.get("nr")
            if nr:
                seen_nrs.add(nr)
    except Exception:
        pass

    end_time = datetime.now(tz=timezone.utc) + timedelta(
        minutes=duration_minutes
    )

    while datetime.now(tz=timezone.utc) < end_time:
        await asyncio.sleep(interval_minutes * 60)

        try:
            if folder:
                feed = net.get_filtered_feed(
                    FolderFilter(folder)
                )["feed"]
            else:
                feed = net.get_feed(limit=50, offset=0)["feed"]
        except Exception:
            continue

        for post in feed:
            nr = post.get("nr")
            if not nr or nr in seen_nrs:
                continue
            seen_nrs.add(nr)

            subj = html.unescape(
                post.get("subject", "")
            )
            # Quick check on subject for deadline keywords
            if _DEADLINE_KEYWORDS.search(subj):
                yield (
                    f"\u23f0 Deadline alert: **@{nr}: {subj}**"
                )
                continue

            # Deep check: fetch full post
            try:
                full = net.get_post(nr)
                all_text = subj + "\n"
                hist = full.get("history", [])
                if hist:
                    all_text += html_to_markdown(
                        hist[0].get("content", "")
                    )
                if _DEADLINE_KEYWORDS.search(all_text):
                    hits = _extract_deadline_lines(all_text)
                    detail = hits[0] if hits else ""
                    yield (
                        f"\u23f0 Deadline alert: "
                        f"**@{nr}: {subj}**"
                        + (f"\n  > {detail}" if detail else "")
                    )
            except Exception:
                continue

    yield "\u23f0 Deadline watch ended."


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    """Entry point for the piazza-mcp-poke command."""
    _login()

    transport = os.environ.get("TRANSPORT", "streamable-http").lower()
    port = int(os.environ.get("PORT", "8247"))

    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        # Wrap the FastMCP app with PokeCallbackMiddleware so
        # @with_callbacks tools can send async updates to Poke.
        app = PokeCallbackMiddleware(
            mcp.http_app(
                transport="streamable-http",
            )
        )
        uvicorn.run(app, host="0.0.0.0", port=port)
