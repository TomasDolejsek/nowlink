# NowLink — Technical Roadmap

> **Project:** NowLink — A well-crafted MCP server bridging Claude and ServiceNow
> **Brand:** NowCraft
> **Solo developer**
> **Started:** May 2026
> **Updated:** June 2026 — v0.4 complete (proof-of-concept)

---

## Guiding Principles

- Ship something working before talking about it publicly
- Each version must be demonstrable on a PDI
- Document every decision as you go — it becomes newsletter content
- Credentials handled correctly from day one, never retrofitted
- Small, well-described tools beat many poorly-described ones

---

## Version Overview

| Version | Focus | Timeline | Status |
|---------|-------|----------|--------|
| v0.0 — Foundation | Project setup, auth, skeleton | Week 1 | ✅ Complete |
| v0.1 — Read | Query, get record, describe table | Week 1 | ✅ Complete |
| v0.2 — Write | Create and update single records | Week 2 | ✅ Complete |
| v0.3 — Safe Bulk | Preview + execute bulk operations | Week 3 | ✅ Complete |
| v0.4 — Flows | Flow Designer bridge, trigger subflows | Week 4 | ✅ Complete (proof-of-concept) |
| v0.5 — Smart Flows | Input discovery, validate before trigger | Week 5 | |
| v0.6 — Extensible | Custom tool API, community tools, PyPI | Week 6 | |

---

## v0.0 — Foundation ✅
**Timeline: Week 1 — Complete**
**Goal: A working skeleton that connects to ServiceNow and appears in Claude Desktop**

### Deliverable ✅
Running `nowlink init` → `nowlink connect` → restart Claude Desktop → Claude can ping your PDI.

### What was built
- OAuth authentication via Resource Owner Password Credential grant
- Credentials stored in Windows Credential Manager via `keyring`
- Four CLI commands: `nowlink init`, `nowlink whoami`, `nowlink connect`, `nowlink serve`
- FastMCP server with `ping` tool connected to Claude Desktop

### Key decisions
- `pyproject.toml` with setuptools backend (not hatchling — editable install issues on Windows)
- `keyring` for secrets, `.env` for non-secret config only
- Proactive token refresh 60 seconds before expiry
- Resource Owner Password Credential grant (not Authorization Code — no browser needed)
- Integration user must be Human identity type — Machine/Internal Integration User blocks password grant
- "Allow access only to APIs in selected scope" must be unchecked in OAuth Application Registry

### Known issues carried forward
- `verify=False` on all httpx calls — needs proper SSL fix
- Hardcoded venv path in `nowlink connect` — needs auto-detection
- No logging yet

---

## v0.1 — Read ✅
**Timeline: Week 1 — Complete**
**Goal: Claude can query any ServiceNow table with clean, token-optimised results**

### Deliverable ✅
Claude can answer questions about any ServiceNow table on a PDI using natural language, with clean readable responses and no hallucinated fields.

### What was built
- `nowlink/client.py` — async HTTP client with pagination, error handling, retry
- `nowlink/shaper.py` — reference field resolver, value mapper, field allowlists
- Three MCP tools: `query`, `get_record`, `describe_table`
- Local tool call logging to `~/.nowlink/logs/`

### Key decisions
- Reference fields resolved to display values before returning to Claude
- Field allowlists per table — only relevant fields returned by default
- 50-record hard cap on queries
- Tool descriptions are the primary mechanism for Claude to pick the right tool

---

## v0.2 — Write ✅
**Timeline: Week 2 — Complete**
**Goal: Claude can create and update single records safely**

### Deliverable ✅
Claude can create and update single ServiceNow records with a mandatory preview-before-write step and full audit logging.

### What was built
- `nowlink/safety.py` — write logger, field diff, field validation
- Two MCP tools: `create_record`, `update_record`
- Confirmation pattern: `confirm=False` (preview) → user approves → `confirm=True` (execute)
- Write log at `~/.nowlink/logs/writes-YYYY-MM-DD.log`
- Post-timeout verification for both POST and PATCH (PDI reliability)
- `caller_id` auto-injection for incident creates

### Key decisions
- PATCH not PUT for updates — only changed fields overwritten
- Tool descriptions enforce single-record constraint ("never use for multiple records")
- Priority is a calculated field — set impact+urgency, not priority directly
- `sys_dictionary.mandatory` is not enforced by the REST API — NowLink validates as UX layer

---

## v0.3 — Safe Bulk ✅
**Timeline: Week 3 — Complete**
**Goal: Claude can safely perform bulk operations with hard guardrails**

### Deliverable ✅
Claude can safely update up to 500 ServiceNow records at once with mandatory preview, session tokens, and complete audit trail.

### What was built
- Three MCP tools: `bulk_preview`, `bulk_execute`, `get_write_log`
- In-memory session token dict `_bulk_tokens` with 5-minute TTL
- Batch API execution in chunks of 50, sub-request statuses ignored
- Post-execute re-count as source of truth (not batch response codes)
- Dynamic mandatory field validation via `get_table_ancestry()` + `get_mandatory_fields()`
- 20 new unit tests for token generation, expiry, immutability, consumption

### Key decisions
- `confirmed` parameter removed from `bulk_execute` — two-turn UX enforced via tool descriptions alone
- ServiceNow Batch API requires base64-encoded request bodies — undocumented
- PDI batch sub-requests return 500 for writes that actually completed — ignore and re-count
- `sys_dictionary.mandatory` not enforced by REST API — confirmed by extensive testing
- Tool description: "STOP after calling bulk_preview" is the actual safety mechanism

---

## v0.4 — Flows ✅
**Timeline: Week 4 — Complete (proof-of-concept)**
**Goal: Claude can trigger Flow Designer subflows and check execution status**

### Deliverable ✅
Claude can list available subflows, trigger them by name with inputs, and check execution status. The NowLink Flow Bridge is installed on the ServiceNow instance via `nowlink setup-flows`.

### What was built

**The Flow Bridge** — a Scripted REST API created on the instance via the Table API:
- `POST /api/x_nowlink/nowlink_flow_bridge/trigger-subflow`
- `POST /api/x_nowlink/nowlink_flow_bridge/trigger-flow`
- `POST /api/x_nowlink/nowlink_flow_bridge/trigger-action`

All three endpoints call `sn_fd.FlowAPI` server-side and return an execution ID.

**Three MCP tools:** `list_subflows`, `trigger_subflow`, `get_flow_status`

**One CLI command:** `nowlink setup-flows` — installs the bridge idempotently, recovers from PDI timeouts using fresh-client verification

**client.py additions:** `setup_flow_bridge()`, `trigger_subflow()`, `get_flow_status()`, `list_subflows()`

### Key decisions

- There is no direct REST endpoint for FlowAPI — `/api/now/flow_api` returns 400. The bridge is the only path on a standard instance without Integration Hub Enterprise
- Subflows, not flows, are the correct unit for programmatic triggering. Flows need a platform trigger event; subflows are designed to be called with arbitrary inputs
- `sys_hub_flow` stores both flows and subflows; filter is `type=subflow` (not `sys_class_name=sys_hub_subflow` — both share the same class)
- `FlowRunnerResult.getExecutionId()` is the correct call, not `String(result)` which returns `[object FlowRunnerResult]`
- Post-timeout verification must use a fresh `httpx.Client` — the same client that timed out returns empty results on subsequent calls
- `web_service_admin` role required for bridge setup only — not for day-to-day subflow triggering
- Bridge idempotency checks both definition AND all operations — early returns only if everything is present

### Known limitation: input discovery not implemented

Subflows, flows, and actions each declare their own named input variables. Without querying the Flow Designer definition, the caller must know the correct variable names in advance. Passing wrong names causes silent failures — the inputs are ignored.

`trigger_flow` and `trigger_action` bridge endpoints exist but are not exposed as MCP tools in v0.4 for this reason.

---

## v0.5 — Smart Flows
**Timeline: Week 5**
**Goal: Claude can discover subflow inputs, validate before triggering, and trigger flows and actions safely**

### Steps

**Input discovery**
- [ ] Query `sys_hub_flow_input` (or equivalent) for declared input variables per subflow
- [ ] Return input names, types, labels, and whether mandatory
- [ ] Add `describe_subflow` tool — shows what inputs a subflow expects before Claude triggers it

**Smart trigger_subflow**
- [ ] Before triggering: fetch declared inputs for the subflow
- [ ] Validate provided inputs against declarations — type check, mandatory check
- [ ] If inputs missing: Claude asks user for them before triggering
- [ ] If wrong keys provided: warn user, suggest correct names

**Expose trigger_flow and trigger_action**
- [ ] Add `list_flows` tool — flows only, with trigger type and input requirements
- [ ] Add `list_actions` tool — actions, with input requirements
- [ ] Add `trigger_flow` tool — with input validation against flow definition
- [ ] Add `trigger_action` tool — with input validation against action definition
- [ ] All three use the existing bridge endpoints

**Testing on PDI**
- [ ] Build a test subflow with 2-3 declared inputs of different types
- [ ] Ask Claude to trigger it — verify it discovers and validates inputs
- [ ] Provide wrong input names — verify Claude catches and corrects
- [ ] Provide missing mandatory input — verify Claude asks before triggering
- [ ] Trigger a flow with correct record context — verify execution

### Deliverable
Claude can trigger any Flow Designer subflow, flow, or action by name with full input validation. If inputs are missing or wrong, Claude asks the user before triggering — not after.

---

## v0.6 — Extensible
**Timeline: Week 6**
**Goal: Any developer can add custom tools without touching NowLink's core code. NowLink on PyPI.**

### Steps

**Plugin architecture**
- [ ] Design plugin API — simple Python decorator pattern
- [ ] Plugin discovery: scan `~/.nowlink/tools/` directory
- [ ] Define plugin access surface: ServiceNow client, shaper, safety logger
- [ ] Write plugin specification in `docs/writing-plugins.md`

**Plugin loader**
- [ ] `nowlink/plugins.py` — auto-discover and register `.py` files in tools dir
- [ ] Validate plugin structure on load — fail gracefully with clear error
- [ ] Log which plugins loaded successfully on startup

**Plugin SDK**
- [ ] `nowlink/sdk.py` — `tool` decorator, `servicenow` client, `shaper`, `logger`

**Example plugins**
- [ ] `nowlink-itsm` — get my incidents, get team queue, SLA breach risk
- [ ] `nowlink-cmdb` — CMDB health checks, CI relationship queries

**CLI commands**
- [ ] `nowlink add-tool <path>` — copy to `~/.nowlink/tools/`
- [ ] `nowlink list-tools` — show loaded tools with descriptions
- [ ] `nowlink remove-tool <name>` — remove from tools directory
- [ ] `nowlink reload` — restart server to pick up new tools

**PyPI publish**
- [ ] Finalize `pyproject.toml` with all metadata
- [ ] Write comprehensive `README.md` — install, quickstart, plugin guide
- [ ] Publish v0.6.0 to PyPI
- [ ] Verify `pip install nowlink` works cleanly on a fresh machine

### Deliverable
Any Python developer can write a custom NowLink tool in under 30 lines and register it with one command. NowLink is on PyPI.

---

## Cross-Version Tasks

**Documentation (ongoing)**
- [ ] Keep `CHANGELOG.md` updated after every version
- [ ] Document every design decision in `docs/decisions/` — these become newsletter posts
- [ ] Screenshot or screen-record every working demo — content for LinkedIn

**Code quality (ongoing)**
- [ ] Write unit tests for every module as you build it — especially the shaper
- [ ] Run `ruff` for linting
- [ ] Keep dependencies minimal and pinned

**PDI hygiene**
- [ ] Never store real client data on PDI
- [ ] Keep PDI active — log in every 10 days to prevent hibernation
- [ ] Back up update sets to GitHub before major PDI changes

---

## Tech Stack Summary

| Component | Library | Why |
|-----------|---------|-----|
| MCP server | `fastmcp` | Standard, well-maintained |
| HTTP client | `httpx` | Async, modern, type-safe |
| Credential storage | `keyring` | OS keychain, cross-platform |
| Config | `python-dotenv` | Non-secret config only |
| CLI | `typer` | Clean CLI from type hints |
| Output formatting | `rich` | Beautiful terminal output |
| Testing | `pytest` | Standard |
| Linting | `ruff` | Fast, opinionated |
