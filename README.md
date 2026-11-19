# TickTick MCP Server

<!-- Add relevant badges here -->
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
<!-- [![PyPI version](https://badge.fury.io/py/your-package-name.svg)](https://badge.fury.io/py/your-package-name) -->

![](images/ticktick_img.webp)

Enhance your TickTick workflow with this MCP server. Built using the **official TickTick OpenAPI v1** for reliable, real-time sync, with optional unofficial API support for advanced features like pinning tasks and activity logs.

## ✨ Features

This server provides comprehensive access to TickTick functionalities via MCP tools:

* **Task Management:** Create, update, delete, complete, and move tasks with full support for subtasks, reminders, recurrence, and priorities.
* **Project Management:** List, create, update, and delete projects. Get all tasks from a project including Inbox.
* **Advanced Filtering:** Filter tasks by status, project, tag, priority, and date ranges (client-side filtering).
* **Unofficial API Features** (optional):
  * Pin/unpin tasks to keep important items at the top
  * Set `repeatFrom` (repeat from due date vs completion date)
  * View task and project activity logs

## 📋 Table of Contents

- [Local Installation](#️-local-installation)
- [Cloud Deployment](#️-cloud-deployment)
  - [Set up your own server, step by step](#set-up-your-own-server-step-by-step)
  - [Connect it to Claude, ChatGPT or another AI app](#connect-it-to-claude-chatgpt-or-another-ai-app)
- [Tools Reference](#-tools-reference)
- [Sample Agent Prompt](#-sample-agent-prompt)
- [Contributing](#-contributing)
- [License](#-license)

## 🖥️ Local Installation

### Prerequisites

* Python >= 3.10
* A TickTick account
* TickTick API credentials (Client ID, Client Secret)

### Setup

1. **Register a TickTick Application:**
   * Go to the [TickTick Developer Portal](https://developer.ticktick.com/docs#/openapi) and log in
   * Click on `Manage Apps` in the top right corner
   * Register a new app by clicking the `+App Name` button
   * Note down the generated `Client ID` and `Client Secret`
   * For `OAuth Redirect URL`, enter `http://localhost:8080/callback` (or any URL you prefer)

2. **Create Environment File:**

   Create a `.env` file at `~/.config/ticktick-mcp/.env`:

   ```dotenv
   # Required: OAuth credentials from Step 1
   TICKTICK_CLIENT_ID=your_client_id
   TICKTICK_CLIENT_SECRET=your_client_secret
   TICKTICK_REDIRECT_URI=http://localhost:8080/callback

   # Required: Your access token (obtained via OAuth flow)
   TICKTICK_ACCESS_TOKEN=your_access_token

   # Required for Inbox access: Your TickTick user ID
   # Find this in your TickTick profile or from API responses
   TICKTICK_USER_ID=115085635

   # Optional: For unofficial API features (pins, repeatFrom, activity logs)
   TICKTICK_USERNAME=your_ticktick_email
   TICKTICK_PASSWORD=your_ticktick_password
   ```

3. **Get Your Access Token:**

   The easiest way is the cloud server below: connecting it in your AI app signs
   you in to TickTick in the browser and saves the token on the server. Visiting
   `/oauth/start` on a running server does the same sign-in by hand. The token is
   saved in `DATA_DIR` and used from then on; it is never shown on screen.

### Running the Server

**Via an MCP Client (Recommended):**

Configure your MCP client (Claude Desktop, VS Code Agent Mode, etc.):

```json
{
  "mcpServers": {
    "ticktick": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/michaelzrork/ticktick-mcp.git",
        "ticktick-mcp"
      ]
    }
  }
}
```

## ☁️ Cloud Deployment

Deploy to Railway (or any host) for 24/7 availability. The server is an OAuth 2.1
authorization server for its own MCP endpoint: **connecting the connector in
Claude opens TickTick's sign-in page in your browser**, and only the owner's
TickTick account is let in.

### Set up your own server, step by step

This server is for one person: you. Each person who wants it runs their own copy
with their own TickTick account. It takes about 20 minutes.

**1. Copy the code.** Fork this repository on GitHub (the **Fork** button,
top right), so you have your own copy for Railway to deploy.

**2. Make a TickTick app.** This is what lets your server ask TickTick for
access to your tasks.
1. Go to the [TickTick Developer Portal](https://developer.ticktick.com) and sign in.
2. Click **Manage Apps** (top right), then **+App Name**, and give it any name.
3. Copy the **Client ID** and **Client Secret**. You'll paste them into Railway in step 4.
4. Leave the **OAuth redirect URL** for now. You'll fill it in at step 5, once
   your server has an address.

**3. Create the server on Railway.**
1. At [Railway](https://railway.app), choose **New Project → Deploy from GitHub repo**
   and pick your fork.
2. Open the new service, go to **Settings**:
   - **Deploy → Custom Start Command:** `uv run ticktick-mcp`
   - **Networking → Generate Domain.** You get an address like
     `https://your-name.up.railway.app`. This is **your server's address**;
     everything below uses it.
3. Add a **volume**. Right-click the service (or use the command palette) → **Add
   Volume**, mount path `/data`. This is where the server keeps your sign-in, so a
   redeploy doesn't log you out.

**4. Set the variables.** In the service's **Variables** tab, add:

| Variable | Value |
|---|---|
| `TICKTICK_CLIENT_ID` | the Client ID from step 2 |
| `TICKTICK_CLIENT_SECRET` | the Client Secret from step 2 |
| `MCP_TRANSPORT` | `http` |
| `DATA_DIR` | `/data` |
| `TICKTICK_USERNAME` | your TickTick email (for the unofficial tools, see step 6) |
| `TICKTICK_PASSWORD` | your TickTick password (same) |

`TICKTICK_USER_ID` is optional but recommended: it turns on Inbox access and is
the strongest check that only your account can sign in. Find it after setup:
the Inbox's project id is `inbox<your id>` (for example, `inbox115085635` means
`115085635`).

Railway redeploys after you save. Open `https://<your server's address>/health`:
it should say `"status": "ok"`.

**5. Tell TickTick your server's address.** Back in the TickTick Developer Portal
→ **Manage Apps** → your app, set **OAuth redirect URL** to exactly:

```
https://<your server's address>/oauth/callback
```

`/health` shows the exact value the server expects, as `ticktick_redirect_uri`.
If the two don't match character for character, TickTick refuses the sign-in.

**6. Turn on the unofficial tools** (pin and unpin, repeat from completion,
activity logs, subtasks). TickTick only offers these through its private web
login, which the browser sign-in can't reach, so the server logs in with your
TickTick email and password from step 4.
- **Check it worked:** open `https://<your server's address>/status`. Under
  `unofficial_api` it should show `"connected": true`. If not, `last_error`
  says why (it never shows your password).
- **If you sign in to TickTick with Google or Apple** (no TickTick password), or
  you use two-factor authentication, use a session token instead of the password:
  sign in at ticktick.com in a desktop browser, open DevTools → **Application** →
  **Cookies** → `https://ticktick.com`, copy the value of the cookie named `t`,
  and save it as `TICKTICK_SESSION_TOKEN` (you can then leave out
  `TICKTICK_USERNAME` and `TICKTICK_PASSWORD`). The token lasts until you sign
  out of ticktick.com in that browser; if the tools stop working, copy a new one.
- **If `/status` says `need_upgrade_client`**, TickTick raised the minimum web
  app version it accepts. Add `TICKTICK_DEVICE_VERSION` with a higher number than
  the one `/status` shows (for example `8500`).
- Your password lives only in Railway's variables, and it gives full access to
  your TickTick account. Keep your Railway account behind two-factor
  authentication, and don't share the project.

If you skip this step, everything else still works; the `unofficial_*` tools
just answer that they're unavailable.

### Connect it to Claude, ChatGPT or another AI app

Your connector URL is your server's address plus `/mcp`:

```
https://<your server's address>/mcp
```

In every app, adding it opens TickTick's sign-in in your browser. Sign in with
**your** account and click Allow. Only the account the server was set up for is
let in.

**Claude (claude.ai, the desktop app, and the mobile apps)**
1. Settings → **Connectors** → **Add custom connector**.
2. Name it (for example, TickTick), paste the connector URL, and click **Add**.
3. Click **Connect** and sign in to TickTick. Connectors added on the web show
   up in the desktop and mobile apps too.

**ChatGPT** (custom connectors need a plan that allows them, and Developer mode)
1. Settings → **Apps & Connectors** → **Advanced settings** → turn on
   **Developer mode**.
2. Back in **Apps & Connectors**, click **Create**, paste the connector URL,
   choose **OAuth** for authentication, and save.
3. Sign in to TickTick when it asks.

**Claude Code**

```bash
claude mcp add --transport http ticktick https://<your server's address>/mcp
```

Then run `/mcp` inside Claude Code and choose the TickTick server to sign in.

**Other apps** that support remote MCP servers (Streamable HTTP with OAuth): give
them the same connector URL. Apps that only support the old SSE transport can't
use this server once `/mcp` has been connected, because `/sse` closes itself for
safety (below).

**Check it's working:** ask your AI app to list your TickTick projects. Then ask
it to pin a task and unpin it; if that works, the unofficial tools are on too.

### Sign-in: how connecting works

1. In Claude: Settings → Connectors → **Add custom connector**, URL
   `https://<your-deployment>/mcp`.
2. Claude registers itself and sends you to `/authorize`, which forwards you to
   TickTick's own login (`https://ticktick.com/oauth/authorize`, scopes
   `tasks:read tasks:write`).
3. TickTick sends you back to `https://<your-deployment>/oauth/callback`. The
   server trades the code for a TickTick token, **checks the account is the
   owner's**, saves the token in its store (`DATA_DIR`), and hands Claude its own
   access token (1 hour) and refresh token (90 days, rotated on use).

**Who counts as the owner.** TickTick's official API has no user id (its
`/user/info` returns only a name and an avatar), so the account is recognized by
what can be read: the numeric id from Inbox tasks (`inbox<userId>`) when the
Inbox has any, compared with `TICKTICK_USER_ID`; otherwise the display name plus
at least half of the owner's project ids. The reference is the account behind
`TICKTICK_ACCESS_TOKEN` until the first sign-in, then the last accepted sign-in.
With neither, the first sign-in is accepted and locked in. Anyone else gets
"This server belongs to someone else" and nothing is saved.

**Token precedence.** The token from the browser sign-in is used while it is
valid; `TICKTICK_ACCESS_TOKEN` is the fallback. TickTick tokens last about 180
days and can't be refreshed. When the saved one runs out, Claude's next refresh
is refused, so it signs in again. `/oauth/start` renews the token by hand: same
sign-in and same owner check, and the token is saved, never shown.

**Register the redirect URI.** In the [TickTick developer portal](https://developer.ticktick.com) →
**Manage Apps** → your app → **OAuth redirect URL**, enter exactly
`https://<your-deployment>/oauth/callback`. `/health` shows the exact value the
server sends (`ticktick_redirect_uri`).

**The old `/sse` endpoint** has no sign-in at all: anyone with the URL can use it.
It stays on only until an AI app first connects through `/mcp`, then it closes
for good (410). `LEGACY_SSE=on|off` overrides that.

### Environment Variables

```bash
# Required: the TickTick app (developer portal)
TICKTICK_CLIENT_ID=your_client_id
TICKTICK_CLIENT_SECRET=your_client_secret

# Required for the cloud server
MCP_TRANSPORT=http            # "sse" is accepted as the old name
DATA_DIR=/data                # a mounted volume: sign-in store, TickTick token, unofficial session

# Optional
PUBLIC_URL=https://<your-deployment>   # defaults to https://$RAILWAY_PUBLIC_DOMAIN
TICKTICK_ACCESS_TOKEN=...              # fallback token until the browser sign-in has run
TICKTICK_USER_ID=115085635             # Inbox access, and the strongest owner check
TICKTICK_REDIRECT_URI=...              # used only if it already points at <public url>/oauth/callback
LEGACY_SSE=on|off

# Optional: unofficial API (pin, repeatFrom, activity logs, subtasks)
TICKTICK_USERNAME=your_ticktick_email
TICKTICK_PASSWORD=your_ticktick_password
TICKTICK_SESSION_TOKEN=<the "t" cookie from a signed-in ticktick.com session>
TICKTICK_DEVICE_VERSION=8240           # web client build sent in x-device (see below)
TICKTICK_DEVICE_ID=<24 lowercase hex chars>
TICKTICK_DEFER_LOGIN=1                 # authenticate on first use, not at startup
```

### The unofficial API (pin, repeatFrom, activity)

These tools use TickTick's private web API, which wants a session cookie (`t`).
There is no OAuth for it, and the browser sign-in above can't capture that cookie:
it belongs to ticktick.com, and no other site can read it. So it is obtained one
of two ways:

* **Username and password** (`TICKTICK_USERNAME` / `TICKTICK_PASSWORD`). The
  login asks for `remember=true`, and the session is cached in `DATA_DIR`, so
  a redeploy doesn't log in again.
* **A session token** (`TICKTICK_SESSION_TOKEN`). Sign in at ticktick.com, then
  DevTools → Application → Cookies → `https://ticktick.com` → copy `t`. Use this
  for an account made with Google/Apple sign-in that has no TickTick password,
  or one with two-factor authentication, which the password login can't answer.

**`need_upgrade_client` (2026-11).** TickTick refuses logins from web-client
builds it considers too old, before it looks at the password: HTTP 500
`{"errorCode":"need_upgrade_client"}`. This code sent build `6260` and now sends
`8240` (the n8n TickTick node made the same fix). If the error comes back, raise
`TICKTICK_DEVICE_VERSION`.

When the unofficial API is unavailable, every `unofficial_*` tool returns
`{"error": <the real reason>, "unofficial_api_unavailable": true, "hint": ...}`
instead of failing, and the official `ticktick_*` tools keep working. `/status`
shows the details (never the token): `last_error`, `last_login_diagnostics`,
`retry_in_seconds`, `device_version`.

**Rate limits and device identity.** The login endpoint throttles hard (429).
Each install sends its own device id (generated once and persisted, or pinned
with `TICKTICK_DEVICE_ID`), so it doesn't share a rate-limit bucket with every
other copy of this code. Failures back off from 30s up to 15 min (or whatever
`Retry-After` asks for). A refusal that retrying can't fix (`need_upgrade_client`,
2FA, `username_password_not_match`) waits an hour.

### Endpoints

| Path | What |
|------|------|
| `/mcp` | The connector URL (Streamable HTTP, sign-in required) |
| `/authorize`, `/token`, `/register`, `/revoke`, `/.well-known/*` | OAuth, from the MCP SDK |
| `/oauth/callback` | TickTick's redirect back |
| `/oauth/start` | Renew the server's TickTick token in the browser |
| `/health`, `/status` | State, never secrets |
| `/favicon.ico`, `/favicon.svg`, `/apple-touch-icon.png`, `/icon.png` | The connector icon (also in MCP `serverInfo.icons`) |
| `/sse` | Old unauthenticated endpoint, closes itself once `/mcp` is used |

## 🔧 Tools Reference

### Task Tools

| Tool | Description |
|------|-------------|
| `ticktick_get_task` | Get a specific task by ID |
| `ticktick_create_task` | Create a new task with title, dates, reminders, priority, tags |
| `ticktick_create_task_with_subtasks` | Create a task with subtasks in one call |
| `ticktick_update_task` | Update task properties |
| `ticktick_complete_task` | Mark a task as complete |
| `ticktick_delete_task` | Delete a task |
| `ticktick_get_all_tasks` | Get all tasks from all projects |
| `ticktick_filter_tasks` | Filter tasks by status, project, tag, priority, date range |
| `ticktick_move_task` | Move a task to a different project |
| `ticktick_make_subtask` | Convert a task into a subtask of another task |

### Project Tools

| Tool | Description |
|------|-------------|
| `ticktick_list_projects` | Get all projects |
| `ticktick_get_project` | Get a specific project |
| `ticktick_get_project_with_tasks` | Get a project with all its tasks |
| `ticktick_get_inbox_tasks` | Get all tasks from Inbox |
| `ticktick_create_project` | Create a new project |
| `ticktick_update_project` | Update project properties |
| `ticktick_delete_project` | Delete a project |

### Unofficial API Tools

These need `TICKTICK_USERNAME` and `TICKTICK_PASSWORD`, or `TICKTICK_SESSION_TOKEN` (see "The unofficial API" above):

| Tool | Description |
|------|-------------|
| `ticktick_pin_task` | Pin a task to the top of the list |
| `ticktick_unpin_task` | Unpin a task |
| `ticktick_set_repeat_from` | Set whether recurring task repeats from due date or completion date |
| `ticktick_get_task_activity` | Get activity log for a task (repeats, due date changes, completions) |

### Task Properties

When creating or updating tasks:

| Property | Format | Example |
|----------|--------|---------|
| `priority` | 0=None, 1=Low, 3=Medium, 5=High | `5` |
| `start_date` / `due_date` | ISO 8601 with timezone | `"2024-07-26T10:00:00+0000"` |
| `reminders` | RFC 5545 TRIGGER array | `["TRIGGER:PT0S", "TRIGGER:-PT30M"]` |
| `repeat_flag` | RFC 5545 RRULE | `"RRULE:FREQ=DAILY;INTERVAL=1"` |
| `tags` | String array | `["work", "urgent"]` |

## 🤖 Sample Agent Prompt

```
## Persona: Daily Stand-up Agent

- **Role**: AI agent integrated with the user's TickTick account
- **Goal**: Help the user start their day efficiently and manage tasks

---

## Core Workflow

1. **Fetch Today's Tasks**
   - Use `ticktick_filter_tasks` with today's date range to get due tasks
   - Use `ticktick_filter_tasks` with status="completed" to see what was done

2. **Daily Briefing**
   Good morning! Here's your daily stand-up:

   **Tasks Due Today:**
   - Task Name 1 (High priority)
   - Task Name 2

   **Overdue Tasks:**
   - Task Name 3

3. **Task Management**
   - Use `ticktick_create_task` to add new tasks
   - Use `ticktick_complete_task` to mark tasks done
   - Use `ticktick_pin_task` to highlight important tasks
   - Use `ticktick_create_task_with_subtasks` to break down large tasks

4. **Example API Calls**

   // Get today's uncompleted tasks
   ticktick_filter_tasks(
     status="uncompleted",
     due_start_date="2024-07-26",
     due_end_date="2024-07-26"
   )

   // Create a task with subtasks
   ticktick_create_task_with_subtasks(
     title="Project Report",
     project_id="...",
     subtask_titles=["Draft outline", "Write content", "Review"]
   )

   // Pin an important task
   ticktick_pin_task(task_id="...")
```

## 🤝 Contributing

Contributions are welcome! Please feel free to open an issue or submit a pull request.

## 📜 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🔗 See Also

* [TickTick OpenAPI Documentation](https://developer.ticktick.com/docs#/openapi): Official API reference
* [Model Context Protocol](https://modelcontextprotocol.io/): Official MCP documentation
* [MCP Servers Repository](https://github.com/modelcontextprotocol/servers): Reference MCP server implementations
