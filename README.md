# NowLink

> MCP server for ServiceNow

⚠️ Under active development. Not ready for use yet.

---

## What is this?

NowLink is an MCP server that connects AI assistants to ServiceNow.
It shapes raw ServiceNow API responses into clean, token-efficient output
that LLMs can reason about without hallucinating field names.

## Status

| Version | Focus | Week | Status |
|---------|-------|------|--------|
| v0.0 — Foundation | Auth, skeleton, Claude Desktop connection | 1 | ✅ Complete |
| v0.1 — Read | Query, get record, describe table | 1 | ✅ Complete |
| v0.2 — Write | Create and update single records | 2 | Planned |
| v0.3 — Safe Bulk | Preview + execute bulk operations | 3 | Planned |
| v0.4 — Flows | Trigger flows, check execution | 4 | Planned |
| v0.5 — Extensible | Plugin API, PyPI release | 5–6 | Planned |

## Integration user roles

The `nowlink.dev` integration user requires these roles:

| Role | Required for |
|------|-------------|
| `rest_service` | OAuth authentication, basic API access |
| `itil` | Read access to incident, problem, change, task tables |
| `personalize_dictionary` | Read access to `sys_dictionary` (used by `describe_table`) |

## Installation

git clone https://github.com/TomasDolejsek/nowlink
cd nowlink
pip install -e .
nowlink init
nowlink connect
Restart Claude Desktop

## License

MIT
