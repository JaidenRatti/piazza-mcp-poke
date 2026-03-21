# piazza-mcp-poke

A [Piazza](https://piazza.com) integration for [Poke](https://poke.com) — ask your AI assistant about course forums in natural language.

> Forked from [smchase/piazza-mcp](https://github.com/smchase/piazza-mcp). Adds SSE transport, new student-focused tools, and Poke connection helpers.

## What you can ask Poke

- "What's going on with assignment 3 in my distributed systems class?"
- "What are the most common questions people have about the midterm?"
- "Any announcements from the prof this week?"
- "What questions are still unanswered?"
- "What has the instructor said about the final project?"
- "What's new since Monday?"
- "Any updates on posts I'm following?"
- "What are the pinned posts?"
- "When is project 2 due?"
- "Summarize the chaos on a3"
- "Any updates on midterms across all my classes?"
- "Post a question about the grading rubric"
- "Watch my distributed systems class and text me when the prof posts"
- *(8am daily)* "Here's what happened across your classes overnight..."

## Tools

### From upstream
| Tool | Description |
| --- | --- |
| `list_classes()` | List your enrolled Piazza classes |
| `set_class(network_id)` | Select a class, see available folders |
| `search_posts(query, folder, limit)` | Search by keyword, folder, or both |
| `get_post(post_number)` | Read a full post with all answers and follow-ups |

### New for Poke
| Tool | Description |
| --- | --- |
| `get_folder_activity(folder, limit)` | Recent posts by last-modified — "what's happening?" |
| `get_hot_posts(folder, limit)` | Most-discussed posts sorted by follow-up count |
| `get_unanswered(folder, limit)` | Posts with zero answers from anyone |
| `get_announcements(folder, limit)` | Instructor notes: deadlines, extensions, logistics |
| `get_instructor_replies(folder, limit)` | Posts where the instructor has responded |
| `get_recent_posts(since, folder, limit)` | Posts updated since a date — "what's new since Monday?" |
| `get_my_posts(limit)` | Posts you're following (created, answered, or bookmarked) |
| `get_unread_posts(limit)` | Posts with unread updates since you last checked |
| `get_pinned_posts(limit)` | Pinned posts: due dates, syllabus, office hours, etc. |
| `get_deadlines(folder, limit)` | Scan posts for due dates, extensions, submission keywords |
| `summarize_folder_activity(folder, hours)` | Bulleted summary of bugs/clarifications in last N hours |
| `get_my_unread(limit)` | Unread posts on threads you're following only |
| `write_post(subject, content, folder)` | Post a new question (anonymous by default) |
| `write_reply(post_number, content)` | Reply to a thread (anonymous by default) |
| `global_search(query)` | Search across ALL classes at once — no set_class needed |

### Proactive (callback-powered)
These tools use `@with_callbacks` to send ongoing updates back to Poke after the initial response.
| Tool | Description |
| --- | --- |
| `daily_digest()` | Summarize all classes’ last 24h — great as a Kitchen cron automation |
| `watch_class(network_id, interval, duration)` | Poll for new instructor posts and text you when they appear |
| `watch_deadlines(network_id, folder, interval, duration)` | Monitor for new deadline/extension posts and alert you |

## Quick start

### Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) (`brew install uv`)
- Node.js 18+ (for `npx poke`)
- A Poke account (`npx poke@latest login`)
- Your Piazza email and password

### 1. Clone and install

```bash
git clone https://github.com/JaidenRatti/piazza-mcp-poke.git
cd piazza-mcp-poke
uv sync
```

### 2. Start the server

```bash
PIAZZA_EMAIL=you@school.ca PIAZZA_PASSWORD=yourpass uv run piazza-mcp-poke
```

The server starts on `http://localhost:8247/mcp` by default.

### 3. Connect to Poke

**Option A — Tunnel (local dev)**

In a second terminal:

```bash
npx poke@latest tunnel http://localhost:8247/mcp -n "Piazza"
```

**Option B — One-liner**

```bash
PIAZZA_EMAIL=you@school.ca PIAZZA_PASSWORD=yourpass ./poke-setup.sh
```

**Option C — Direct URL (if deployed)**

Go to **Poke → Settings → Connections → Add Integration → Create**, enter:
- Name: `Piazza`
- MCP Server URL: `https://your-host:8247/mcp`

### 4. Talk to Poke

Open Poke and ask away:

> "In my distributed systems class, what are students asking about the latest assignment that the prof has answered?"

## Configuration

| Environment variable | Default | Description |
| --- | --- | --- |
| `PIAZZA_EMAIL` | *(required)* | Your Piazza login email |
| `PIAZZA_PASSWORD` | *(required)* | Your Piazza password |
| `PORT` | `8247` | HTTP port for SSE server |
| `TRANSPORT` | `streamable-http` | `streamable-http` for Poke, `stdio` for Claude/VS Code |

## Proactive notifications

### Option 1: Callback tools (in-conversation)

Ask Poke to watch a class and it’ll text you updates in real-time:

> "Watch my CS 454 class and let me know when the prof posts something"

This uses `@with_callbacks` from the `poke` SDK — the first response comes back immediately, and subsequent updates are POSTed to Poke as they happen.

### Option 2: Kitchen automation (scheduled)

Set up a daily digest in Poke Kitchen (`poke.com/kitchen`):
1. Create a recipe with the Piazza integration
2. Add an automation: schedule `daily` at `8:00 AM`
3. Action: "Run daily_digest and send me the results"

### Option 3: Watcher daemon (standalone)

Run the watcher as a background process that pushes to Poke independently:

```bash
PIAZZA_EMAIL=you@school.ca PIAZZA_PASSWORD=yourpass \
POKE_API_KEY=pk_your_key \
uv run piazza-watcher
```

This sends:
- **Daily digest** at 8am (configurable via `DIGEST_HOUR`)
- **Instant alerts** when a prof posts, an announcement appears, or a deadline-related post is created

| Env var | Default | Description |
| --- | --- | --- |
| `POKE_API_KEY` | *(required)* | Get from `poke.com/kitchen/api-keys` |
| `WATCH_INTERVAL` | `300` | Seconds between polls |
| `DIGEST_HOUR` | `8` | Hour (0-23) for daily digest |
| `WATCH_FOLDERS` | *(all)* | Comma-separated folders to monitor |

## Docker

```bash
docker build -t piazza-mcp-poke .
docker run -e PIAZZA_EMAIL=you@school.ca -e PIAZZA_PASSWORD=yourpass -p 8247:8247 piazza-mcp-poke
```

## Still works with Claude / VS Code

Set `TRANSPORT=stdio` to use the original stdio mode:

```bash
claude mcp add piazza --env PIAZZA_EMAIL=you@school.ca --env PIAZZA_PASSWORD=yourpass --env TRANSPORT=stdio -- uv --directory /path/to/piazza-mcp-poke run piazza-mcp-poke
```

## License

MIT — see [LICENSE](LICENSE).
