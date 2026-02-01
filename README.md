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
| `ticktick_pin_task` | Pin a task to the top of the list |
| `ticktick_unpin_task` | Unpin a task |
| `ticktick_set_repeat_from` | Set whether recurring task repeats from due date or completion date |
| `ticktick_get_task_activity` | Get activity log for a task |
| `ticktick_get_project_activity` | Get activity log for a project |

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
