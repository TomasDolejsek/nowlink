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
> "Update INC0001234 — change the priority to High"
> "Update all open incidents assigned to the networking team to urgency High"

NowLink handles the ServiceNow API calls, shapes the raw responses into clean
token-efficient output, and keeps your credentials in your OS keychain — never in
a file that could accidentally get committed to git.

**100% local.** Your credentials, data, and session tokens never leave your machine.

---

## Status

| Version | Focus | Status |
|---------|-------|--------|
| v0.0 — Foundation | Auth, skeleton, Claude Desktop connection | ✅ Complete |
| v0.1 — Read | Query, get record, describe table | ✅ Complete |
| v0.2 — Write | Create and update single records | ✅ Complete |
| v0.3 — Safe Bulk | Preview + execute bulk operations | ✅ Complete |
| v0.4 — Flows | Trigger flows, check execution | Planned |
| v0.5 — Extensible | Plugin API, PyPI release | Planned |

---

## Tools available in Claude

### Read (v0.1)

| Tool | What it does |
|------|-------------|
| `query` | Find records matching a filter across any table |
| `get_record` | Fetch a single record by number (INC0001234) or sys_id |
| `describe_table` | Show the fields and valid values for any table |

### Write (v0.2)

| Tool | What it does |
|------|-------------|
| `create_record` | Create a new record — always previews before writing |
| `update_record` | Update a single record — shows a diff before writing |

All write operations follow a mandatory two-step pattern:
1. Claude calls the tool with `confirm=False` — shows a preview or diff, writes nothing
2. You say yes — Claude calls again with `confirm=True` and executes the write

### Bulk (v0.3)

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
unchecked. Machine identity type blocks OAuth password grant entirely — ServiceNow
returns `access_denied` with no useful explanation.

**Important:** "Password needs reset" must be unchecked. A headless API user cannot
complete a browser-based password reset — the OAuth flow silently fails.

### Required roles

| Role | Required for |
|------|-------------|
| `rest_service` | OAuth authentication, basic API access |
| `itil` | Read/write access to incident, problem, change, task tables |
| `personalize_dictionary` | Read access to `sys_dictionary` (used by `describe_table`) |

---

## Configuration

Non-secret config lives in `.env` in the project root (never committed to git):

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

Priority is auto-calculated from impact × urgency via a business rule. Setting
`priority` directly gets overwritten immediately. To set P1 — set `impact=1`
and `urgency=1` and let ServiceNow derive priority automatically.

### Mandatory field validation

Before any create, NowLink walks the table inheritance chain via `sys_db_object`
and queries `sys_dictionary` for mandatory fields — including inherited ones. A
custom table extending `task` will have `short_description` flagged as mandatory
if that's how your instance is configured.

Note: `sys_dictionary.mandatory` reflects database-level constraints. The ServiceNow
REST API does not enforce these — the validation is NowLink's pre-flight UX layer.
Fields enforced only via UI Policies (browser-only) will not appear in this check.

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
│   ├── cli.py       # CLI entry points: init, whoami, connect, serve
│   ├── client.py    # ServiceNow Table API HTTP wrapper + bulk operations
│   ├── logger.py    # Tool call logging
│   ├── safety.py    # Field validation, diff generation, write audit logging
│   ├── server.py    # FastMCP server, tool definitions, session token store
│   └── shaper.py    # Data transformation — allowlists, value maps, reference resolution
└── tests/
    ├── test_shaper.py       # 17 tests — data transformation
    ├── test_safety.py       # 16 tests — validation, diff, write logging
    └── test_bulk_tokens.py  # 20 tests — session tokens, hard limit, bulk diff
```

---

## License

MIT
