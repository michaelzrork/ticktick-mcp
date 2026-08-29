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
- [Tools Reference](#-tools-reference)
- [Sample Agent Prompt](#-sample-agent-prompt)
- [Tests](#-tests)
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

   You can obtain an access token through the OAuth flow:
   * Deploy the server locally or to cloud
   * Visit `/oauth/start` endpoint
   * Authorize the application
   * Copy the `access_token` from the response

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

Deploy this MCP server to cloud platforms like Railway, Render, or Fly.io for 24/7 availability.

### Environment Variables

Configure these in your cloud platform:

```bash
# Required: OAuth credentials
TICKTICK_CLIENT_ID=your_client_id
TICKTICK_CLIENT_SECRET=your_client_secret
TICKTICK_REDIRECT_URI=https://<your-deployment-url>/oauth/callback

# Required: Access token (get via /oauth/start after first deploy)
TICKTICK_ACCESS_TOKEN=your_access_token

# Required for Inbox access
TICKTICK_USER_ID=your_user_id

# Optional: For unofficial API features (pins, repeatFrom, activity logs)
TICKTICK_USERNAME=your_ticktick_email
TICKTICK_PASSWORD=your_ticktick_password

# Required: Enable SSE transport for cloud
MCP_TRANSPORT=sse
```

### Getting Your Access Token

1. Deploy with all variables EXCEPT `TICKTICK_ACCESS_TOKEN`
2. Visit `https://<your-deployment-url>/oauth/start`
3. Authorize the application
4. Copy the `access_token` from the JSON response
5. Add `TICKTICK_ACCESS_TOKEN` to your environment variables
6. Redeploy

### Connect from MCP Clients

**From claude.ai:**
1. Go to Settings → Connectors
2. Click "Add custom connector"
3. Enter URL: `https://<your-deployment-url>/sse`

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

These require `TICKTICK_USERNAME` and `TICKTICK_PASSWORD` to be set:

| Tool | Description |
|------|-------------|
| `unofficial_get_task` / `unofficial_get_all` / `unofficial_get_tasks_from_project` / `unofficial_filter_tasks` | Fresh, uncached reads |
| `unofficial_create_task` / `unofficial_update_task` / `unofficial_delete_task` / `unofficial_move_task` | Full CRUD, including recurrence and status the official API can't set |
| `unofficial_pin_task` / `unofficial_unpin_task` | Pin or unpin a task |
| `unofficial_get_task_activity` | Activity log for a task (repeats, due date changes, completions) |
| `unofficial_make_subtask` / `unofficial_remove_subtask` | Nest or un-nest a single task |
| `unofficial_batch_make_subtasks` | Link many parent/child pairs in one `batch/taskParent` call |
| `unofficial_set_task_location` / `unofficial_remove_task_location` | Attach or clear a geofenced location |
| `unofficial_add_checklist_item` / `unofficial_update_checklist_item` / `unofficial_remove_checklist_item` | Manage the `items[]` checklist |
| `unofficial_convert_checklist_item_to_task` / `unofficial_convert_task_to_checklist_item` | Convert between checklist items and tasks |
| `unofficial_experimental_api_call` | Raw v2 API call for experimentation |

### Task Properties

When creating or updating tasks:

| Property | Format | Example |
|----------|--------|---------|
| `priority` | 0=None, 1=Low, 3=Medium, 5=High | `5` |
| `start_date` / `due_date` | Local date or date-time (see below) | `"2024-07-26T10:00:00"` |
| `time_zone` | IANA timezone name | `"America/New_York"` |
| `reminders` | RFC 5545 TRIGGER array | `["TRIGGER:PT0S", "TRIGGER:-PT30M"]` |
| `repeat_flag` | RFC 5545 RRULE | `"RRULE:FREQ=DAILY;INTERVAL=1"` |
| `tags` | String array | `["work", "urgent"]` |
| `location` | Geofence object (see below) | `{"address": "...", "loc": {...}}` |

### Dates and time zones

Dates passed to the unofficial tools are **local wall-clock time in `time_zone`**.
They are converted to UTC before being sent, so `"2026-08-29T09:30:00"` with
`time_zone="America/New_York"` is stored as `2026-08-29T13:30:00.000+0000` and shows
as 9:30 AM in the app. Pass the time the user actually said — don't pre-convert.

- **Timed task** — include a time: `"2026-08-29T09:30:00"`.
- **All-day task** — date only: `"2026-08-29"`. All-day dates are date-only
  semantically and are *not* shifted; TickTick applies the task's `timeZone` itself.
- `is_all_day` is inferred from the date (time component → timed), and an explicit
  value always wins.

### Task locations

`unofficial_set_task_location` attaches a geofenced reminder. The v2 API is unusually
unforgiving here, so the tool normalizes the payload and verifies the write:

```json
"location": {
  "alias": "Mattress Recycling",
  "address": "525 Riverside Ave, Burlington, VT 05401",
  "shortAddress": "525 Riverside Ave",
  "loc": { "latitude": 44.4893668, "longitude": -73.2027386 },
  "radius": 300,
  "transitionType": 1
}
```

- Coordinates **must** be nested under `loc`. Top-level `latitude`/`longitude` and
  GeoJSON `{"type": "Point", "coordinates": [lng, lat]}` are accepted with a `200`
  and then **silently discarded** — the tools rewrite both forms into `loc`.
- A `200` proves nothing. Every location write is read back, and the result carries
  `location_verified`.
- `radius` is in **meters** (default 300). `transitionType`: `1` = arrive, `2` = leave.
- `removed` is server-managed — never set it.

### Batch updates

`POST /api/v2/batch/task` **nulls any field you omit**, so every write in
`unofficial_tools.py` fetches the full task, merges the change, and posts the whole
object back. Follow that read-merge-write pattern for any new tool.

Hierarchy is the exception: `parentId` inside a `batch/task` `{"add": [...]}` payload
is ignored on create, so parent/child links always need a separate
`batch/taskParent` call (`unofficial_batch_make_subtasks`). In its response, a
parent's `childIds` can be stale mid-transaction — the children's `parentId` values
are authoritative.

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

## 🧪 Tests

```bash
uv sync --group dev
uv run pytest
```

The default run is offline — it drives the tools against an in-memory fake of the v2
API and asserts on the exact JSON sent. The live round-trip tests in
`tests/test_live_api.py` create and delete throwaway tasks in your real Inbox and are
opt-in:

```bash
TICKTICK_RUN_LIVE_TESTS=1 uv run pytest tests/test_live_api.py
```

## 🤝 Contributing

Contributions are welcome! Please feel free to open an issue or submit a pull request.

## 📜 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🔗 See Also

* [TickTick OpenAPI Documentation](https://developer.ticktick.com/docs#/openapi): Official API reference
* [Model Context Protocol](https://modelcontextprotocol.io/): Official MCP documentation
* [MCP Servers Repository](https://github.com/modelcontextprotocol/servers): Reference MCP server implementations
