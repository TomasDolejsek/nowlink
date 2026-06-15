# NowLink

> A local MCP server that connects Claude Desktop directly to ServiceNow.

⚠️ Under active development — not yet on PyPI. Install from source.

---

## What is this?

NowLink is an MCP (Model Context Protocol) server written in Python that bridges
Claude Desktop and ServiceNow. Instead of clicking through list views or writing
encoded queries by hand, you ask Claude in plain language:

> "Show me all P1 incidents without an assignment group"
> "Create a test incident and assign it to the networking team"
> "Update all open incidents assigned to the networking team to urgency High"
> "Trigger the onboarding subflow for john.smith"
> "Trigger the change approval flow against CHG0000024"

NowLink handles the ServiceNow API calls, shapes the raw responses into clean
token-efficient output, validates inputs before any write or trigger, and keeps
your credentials in your OS keychain — never in a file.

**100% local.** Your credentials, data, and session tokens never leave your machine.

---

## Status

| Version | Focus | Status |
|---------|-------|--------|
| v0.0 — Foundation | Auth, skeleton, Claude Desktop connection | ✅ Complete |
| v0.1 — Read | Query, get record, describe table | ✅ Complete |
| v0.2 — Write | Create and update single records | ✅ Complete |
| v0.3 — Safe Bulk | Preview + execute bulk operations | ✅ Complete |
| v0.4 — Flows | Flow Designer bridge, trigger subflows | ✅ Complete |
| v0.5 — Smart Flows | Input discovery, validate before trigger, full Flow Designer coverage | ✅ Complete |
| v0.6 — Extensible | Plugin API, PyPI release | Planned |

---

## Tools available in Claude

### Read

| Tool | What it does |
|------|-------------|
| `query` | Find records matching a filter across any table |
| `get_record` | Fetch a single record by number (INC0001234) or sys_id |
| `describe_table` | Show the fields and valid values for any table |

### Write

| Tool | What it does |
|------|-------------|
| `create_record` | Create a new record — always previews before writing |
| `update_record` | Update a single record — shows a diff before writing |

All write operations follow a mandatory two-step pattern:
1. Claude calls the tool with `confirm=False` — shows a preview or diff, writes nothing
2. You say yes — Claude calls again with `confirm=True` and executes the write

### Bulk

| Tool | What it does |
|------|-------------|
| `bulk_preview` | Count matching records, show a before→after sample, generate a session token |
| `bulk_execute` | Execute the bulk update using the token from bulk_preview |
| `get_write_log` | Read NowLink's write audit log — every create and update |

Bulk operations follow a mandatory two-turn pattern:
1. `bulk_preview` — Claude shows count, sample table with before→after, and asks "Shall I execute?"
2. You say yes — Claude calls `bulk_execute` with the session token

The session token encodes the table, filter, and fields. Claude cannot modify the
operation between preview and execute. Token expires after 5 minutes. Hard limit:
500 records per bulk operation.

Every confirmed write and bulk update is logged to `~/.nowlink/logs/writes-YYYY-MM-DD.log`.

**NowLink will never expose a delete operation.** ServiceNow is designed around state —
records are closed and cancelled, not deleted.

### Flow Designer

| Tool | What it does |
|------|-------------|
| `list_subflows` | List all active published subflows with their trigger names |
| `describe_subflow` | Show a subflow's declared input variables before triggering |
| `trigger_subflow` | Trigger a subflow by name — validates inputs before firing |
| `get_flow_status` | Check execution status of a triggered subflow or flow |
| `list_flows` | List all active published flows |
| `describe_flow` | Show what triggers a flow and what record context it expects |
| `trigger_flow` | Trigger a record-triggered flow — asks for a record, then fires via the bridge |
| `list_actions` | List all active published Flow Designer actions |
| `describe_action` | Show a Flow Designer action's declared input variables |
| `trigger_action` | Trigger an action by name — validates inputs before firing |

#### How subflow and action triggering works

NowLink installs a Scripted REST API bridge on your ServiceNow instance (`nowlink setup-flows`).
The bridge exposes three endpoints that call `sn_fd.FlowAPI` server-side — the only way to
trigger Flow Designer programmatically on a standard PDI without Integration Hub Enterprise.

Before triggering, NowLink queries the subflow or action definition, extracts declared input
variables, and validates what you provided. Wrong key name? Claude tells you before firing —
not after.

```
User: "trigger the onboarding subflow with text 'john.smith'"

Claude: The input name is `username`, not `text`.
        Do you want me to trigger it with username: "john.smith" instead?
```

#### How flow triggering works

Flows have platform event triggers — record changes, schedules, catalog submissions.
NowLink inspects each flow before attempting to trigger it:

- **Record-triggered flow** — asks for a record sys_id from the required table,
  then calls the bridge with that record as context
- **Scheduled or event-driven flow** — explains why it cannot be triggered via API
  and suggests rebuilding as a subflow

#### Role requirements for Flow Designer tools

| Tool group | Required role |
|-----------|--------------|
| Subflow tools | `rest_service` |
| Flow tools | `rest_service` |
| Action tools | `rest_service` + `flow_designer` |
| `nowlink setup-flows` | `rest_service` + `web_service_admin` (setup only) |

Action discovery requires `flow_designer` because `sys_hub_action_type_definition`
inherits from `Application File` — the same base class as scripts and business rules.
`web_service_admin` is only needed during bridge installation and can be removed after.

---

## Installation

```bash
git clone https://github.com/TomasDolejsek/nowlink
cd nowlink
pip install -e .
```

### Configure credentials

```bash
nowlink init
```

Prompts for your ServiceNow instance URL, OAuth Client ID, Client Secret, and
integration user credentials. Stores everything in your OS keychain (Windows
Credential Manager on Windows, Keychain on macOS).

### Connect to Claude Desktop

```bash
nowlink connect
```

Writes the NowLink entry to `claude_desktop_config.json`. Restart Claude Desktop
after running this.

### Set up Flow Designer bridge

```bash
nowlink setup-flows
```

Creates the NowLink Flow Bridge Scripted REST API on your instance. Required once
before using any flow, subflow, or action triggering tools. Safe to re-run —
idempotent.

### Verify the connection

```bash
nowlink whoami
```

---

## ServiceNow setup

### OAuth Application Registry

| Setting | Value |
|---------|-------|
| Grant type | Resource Owner Password Credential |
| Client type | Integration as a Service |
| Access token lifespan | 1800 seconds |
| Allow access only to APIs in selected scope | **Unchecked** |

### Integration user

| Setting | Value |
|---------|-------|
| User ID | `nowlink.dev` (or your preferred name) |
| Identity type | Default (not Machine) |
| Internal Integration User | Unchecked |
| Password needs reset | **Unchecked** |

**Important:** Identity type must be Default and Internal Integration User must be
unchecked. Machine identity type blocks OAuth password grant — ServiceNow returns
`access_denied` with no explanation.

**Important:** "Password needs reset" must be unchecked. A headless API user cannot
complete a browser-based password reset — the OAuth flow silently fails.

### Required roles

| Role | Required for |
|------|-------------|
| `rest_service` | OAuth authentication, all basic API access |
| `itil` | Read/write access to incident, problem, change, task tables |
| `personalize_dictionary` | Read access to `sys_dictionary` (used by `describe_table`) |
| `web_service_admin` | Installing the Flow Bridge (`nowlink setup-flows`) — can be removed after |
| `flow_designer` | Action discovery (`list_actions`, `describe_action`, `trigger_action`) |

---

## Configuration

Non-secret config lives in `.env` in the project root:

| Variable | Default | Description |
|----------|---------|-------------|
| `NOWLINK_INSTANCE_URL` | — | Your ServiceNow instance URL |
| `NOWLINK_PAGE_SIZE` | `20` | Default records returned per query |
| `NOWLINK_REQUEST_TIMEOUT` | `60` | HTTP timeout in seconds |
| `NOWLINK_BULK_CHUNK_SLEEP` | `1.0` | Seconds between bulk batch chunks |

Copy `.env.example` to `.env` to get started.

---

## How writes work

### Field values

Write tools require raw ServiceNow coded values, not display labels:

| Field | Raw value | Meaning |
|-------|-----------|---------|
| `priority` | `"1"` | Critical |
| `priority` | `"2"` | High |
| `priority` | `"3"` | Moderate |
| `priority` | `"4"` | Low |
| `impact` | `"1"` | High |
| `urgency` | `"1"` | High |
| `state` (incident) | `"1"` | New |
| `state` (incident) | `"2"` | In Progress |
| `state` (incident) | `"6"` | Resolved |
| `state` (incident) | `"7"` | Closed |

For unfamiliar tables or fields, ask Claude to call `describe_table` first.

### Priority on incidents

Priority is auto-calculated from impact × urgency. Setting `priority` directly
gets overwritten by a business rule. To set P1 — set `impact=1` and `urgency=1`.

### Mandatory field validation

Before any create, NowLink walks the table inheritance chain via `sys_db_object`
and queries `sys_dictionary` for mandatory fields including inherited ones.

Note: `sys_dictionary.mandatory` reflects database-level constraints. Fields
enforced only via UI Policies will not appear in this check.

### caller_id on incident creates

If you don't specify `caller_id` when creating an incident, NowLink automatically
uses the integration user as the caller. Override by providing `caller_id` explicitly.

---

## Development

```bash
# Run all tests
python -m pytest tests/ -v

# Lint
ruff check nowlink/
```

### Project structure

```
nowlink/
├── nowlink/
│   ├── auth.py      # OAuth flow, credential storage, token management
│   ├── cli.py       # CLI entry points: init, whoami, connect, serve, setup-flows
│   ├── client.py    # ServiceNow API wrapper — Table API, bulk, Flow Designer bridge
│   ├── logger.py    # Tool call logging
│   ├── safety.py    # Field validation, diff generation, write audit logging
│   ├── server.py    # FastMCP server — 19 tool definitions, session token store
│   └── shaper.py    # Data transformation — allowlists, value maps, reference resolution
└── tests/
    ├── test_shaper.py           # Data transformation
    ├── test_safety.py           # Validation, diff, write logging
    ├── test_bulk_tokens.py      # Session tokens, hard limit, bulk diff
    ├── test_flows.py            # Flow input parsing, validation, trigger logic
    └── test_server_structure.py # Structural — no duplicate tool registrations
```

---

## License

MIT
