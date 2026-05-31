# nowlink/server.py
# FastMCP server definition and tool registration

import httpx
from fastmcp import FastMCP
from nowlink.auth import get_connection_info, get_valid_token, load_credentials
from nowlink.client import query_records, get_record_by_number, get_record_by_sys_id, \
    describe_table as fetch_table_schema, create_record as client_create, \
    update_record as client_update, get_mandatory_fields, bulk_query as client_bulk_query, \
    bulk_fetch_sys_ids, bulk_update as client_bulk_update
from nowlink.shaper import shape_records, shape_record, shape_table_schema, TABLE_FIELDS
from nowlink.safety import diff_fields, log_write
from nowlink.logger import log_tool_call, log_error

import uuid
from datetime import datetime, timedelta

mcp = FastMCP("nowlink")

# ── Bulk operation session tokens ─────────────────────────────────────────────
# In-memory dict keyed by UUID. Stores the preview state that bulk_execute must
# match. Token expires after 5 minutes. Dies with the server process — user
# re-previews after a restart, which is the correct behaviour.
#
# Structure: {token_uuid: {table, filters, fields_to_set, count, expires_at}}

BULK_TOKEN_TTL_MINUTES = 5
_bulk_tokens: dict[str, dict] = {}


# ── ping ──────────────────────────────────────────────────────────────────────

@mcp.tool()
def ping() -> dict:
    """
    Check if NowLink is connected to ServiceNow and return the instance details.
    Use this to verify the connection is working before running other tools.
    """
    info = get_connection_info()
    token = get_valid_token()
    creds = load_credentials()

    response = httpx.get(
        f"{creds['instance_url']}/api/now/table/sys_user",
        headers={"Authorization": f"Bearer {token}"},
        params={
            "sysparm_query": f"user_name={creds['username']}",
            "sysparm_fields": "user_name,name",
            "sysparm_limit": "1",
        },
        verify=False,
    )

    return {
        "status": "connected",
        "instance": info["instance_url"],
        "user": info["username"],
        "message": f"NowLink is connected to {info['instance_url']} as {info['username']}",
    }


# ── query ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def query(
        table: str,
        filters: str = "",
        limit: int = 20,
) -> dict:
    """
    Query any ServiceNow table and return clean, readable records.

    Use this tool when:
    - The user asks to find, list, search, or show records from any table
    - The user asks questions like "show me all P1 incidents" or "find open problems"
    - You need multiple records (not a single known record)

    Do NOT use this tool to fetch a single known record by number — use get_record instead.

    Parameters:
    - table: ServiceNow table name (e.g. "incident", "problem", "change_request", "task")
    - filters: An encoded ServiceNow query string (e.g. "priority=1^state=1^active=true").
      Leave empty to return the most recent records.
      Common field names: state, priority, assignment_group, assigned_to, caller_id,
      opened_at, resolved_at, short_description (use LIKE for text search).
      Common state values: incident states 1=New 2=InProgress 6=Resolved 7=Closed.
      Common priority values: 1=Critical 2=High 3=Moderate 4=Low.
    - limit: Number of records to return. Default 20. Maximum 50.

    Returns a list of shaped records with human-readable field values.
    Reference fields (like assigned_to) are resolved to display names, not sys_ids.
    Priority and state codes are translated to labels.
    """
    params = {"table": table, "filters": filters, "limit": limit}
    try:
        raw = query_records(table, sysparm_query=filters, limit=limit)
        shaped = shape_records(raw, table)
        log_tool_call("query", params, f"{len(shaped)} records returned")
        return {
            "table": table,
            "count": len(shaped),
            "records": shaped,
        }
    except Exception as e:
        log_error("query", params, str(e))
        return {"error": str(e), "table": table}


# ── get_record ────────────────────────────────────────────────────────────────

@mcp.tool()
def get_record(
        table: str,
        identifier: str,
) -> dict:
    """
    Fetch a single ServiceNow record by its number or sys_id.

    Use this tool when:
    - The user asks about a specific record and provides a number (e.g. INC0001234, PRB0000012)
    - You already know the sys_id of the record you need
    - You need full detail on one specific record

    Do NOT use this tool to search for records — use query instead.

    Parameters:
    - table: ServiceNow table name (e.g. "incident", "problem", "change_request")
    - identifier: The record number (e.g. "INC0001234") or sys_id (32-character hex string).
      Record numbers start with a prefix: INC=incident, PRB=problem, CHG=change_request,
      REQ=sc_request, RITM=sc_req_item, TASK=task.

    Returns a single shaped record with all relevant fields resolved to human-readable values.
    """
    params = {"table": table, "identifier": identifier}
    try:
        # sys_id is a 32-char hex string; numbers have letter prefixes
        if len(identifier) == 32 and all(c in "0123456789abcdef" for c in identifier.lower()):
            raw = get_record_by_sys_id(table, identifier)
        else:
            raw = get_record_by_number(table, identifier)

        shaped = shape_record(raw, table)
        log_tool_call("get_record", params, f"returned {identifier}")
        return shaped
    except Exception as e:
        log_error("get_record", params, str(e))
        return {"error": str(e)}


# ── describe_table ────────────────────────────────────────────────────────────

@mcp.tool()
def describe_table(table: str) -> dict:
    """
    Return the field schema for a ServiceNow table.

    Use this tool when:
    - The user asks what fields a table has
    - You need to know field names before constructing a query filter
    - You need to understand what values are valid for a field
    - A previous query returned unexpected fields and you need to check the schema

    Parameters:
    - table: ServiceNow table name (e.g. "incident", "problem", "change_request", "cmdb_ci")

    Returns up to 30 fields with their API field name, display label, data type,
    whether they are mandatory, and the table they reference (for reference fields).
    Mandatory fields are listed first.
    """
    params = {"table": table}
    try:
        # For tables with a known allowlist, build the schema from that instead of
        # querying sys_dictionary. sys_dictionary misses inherited fields — e.g.
        # short_description on incident is inherited from task and won't appear
        # under name=incident. The allowlist is what NowLink actually returns,
        # so it's the accurate answer to "what fields does this table have?"
        if table in TABLE_FIELDS:
            mandatory = set()
            shaped = [
                {
                    "field": field,
                    "label": field.replace("_", " ").title(),
                    "type": "string",
                    **({"mandatory": True} if field in mandatory else {}),
                }
                for field in TABLE_FIELDS[table]
            ]
            # Mandatory fields first, then preserve allowlist order
            shaped.sort(key=lambda x: (not x.get("mandatory", False), TABLE_FIELDS[table].index(x["field"])))
            source = "allowlist"
        else:
            # Unknown table — fall back to sys_dictionary
            raw_fields = fetch_table_schema(table)
            shaped = shape_table_schema(raw_fields)
            shaped = shaped[:30]
            source = "sys_dictionary"

        log_tool_call("describe_table", params, f"{len(shaped)} fields returned for {table} (source: {source})")
        return {
            "table": table,
            "field_count": len(shaped),
            "fields": shaped,
            "source": source,
        }
    except Exception as e:
        log_error("describe_table", params, str(e))
        return {"error": str(e), "table": table}


# ── create_record ─────────────────────────────────────────────────────────────

@mcp.tool()
def create_record(
        table: str,
        fields: dict,
        confirm: bool = False,
) -> dict:
    """
    Create a new record in a ServiceNow table.

    IMPORTANT — TWO-STEP PATTERN. Always call this tool TWICE:
      Step 1: confirm=False (default) — shows a preview of what WOULD be created.
              Present this preview to the user and ask "Shall I go ahead?"
      Step 2: confirm=True — only after the user explicitly says yes.

    Never skip the preview step. Never call with confirm=True on the first attempt.

    Parameters:
    - table:   ServiceNow table name (e.g. "incident", "problem", "change_request")
    - fields:  Dict of field names and values to set on the new record.
               CRITICAL — field values must be RAW ServiceNow codes, not display labels.
               For unfamiliar tables or fields, call describe_table first to check valid values.

               Common values for standard ITSM tables:
                 priority:  "1"=Critical  "2"=High  "3"=Moderate  "4"=Low
                 impact:    "1"=High  "2"=Medium  "3"=Low
                 urgency:   "1"=High  "2"=Medium  "3"=Low
                 state (incident): "1"=New  "2"=In Progress  "6"=Resolved  "7"=Closed
                 state (problem):  "101"=New  "106"=Resolved  "107"=Closed
                 state (change):   "-5"=New  "-1"=Implement  "3"=Closed
               Reference fields (assigned_to, caller_id, assignment_group) accept
               sys_id or user_name/name — ServiceNow resolves them.
               caller_id: auto-filled from credentials for incident if not provided.
               For custom tables: call describe_table to discover field names and types.

    Returns on preview (confirm=False):
      {"preview": true, "table": ..., "fields_to_create": {...}}
    Returns on success (confirm=True):
      {"created": true, "table": ..., "number": "INC0012345", "sys_id": "...", "record": {...}}
    If ServiceNow rejects the create (missing required fields, ACL, etc.):
      {"error": "ServiceNow error on create on incident: ..."}
    """
    params = {"table": table, "fields": fields, "confirm": confirm}

    try:
        # Inject caller_id default for incident if not provided.
        if table == "incident" and "caller_id" not in fields:
            creds = load_credentials()
            fields = {**fields, "caller_id": creds["username"]}

        # Pre-flight mandatory field validation.
        # Walks the full table inheritance chain via sys_db_object, then queries
        # sys_dictionary for mandatory=true fields across the chain.
        # This is NowLink's validation layer — the REST API may accept records
        # missing these fields, but we warn the user before they confirm.
        # Falls back to no validation if sys_db_object is inaccessible.
        mandatory_fields = get_mandatory_fields(table)
        missing = [
            f for f in mandatory_fields
            if not str(fields.get(f, "")).strip()
        ]

        if not confirm:
            log_tool_call("create_record:preview", params, f"preview for {table}")
            return {
                "preview": True,
                "table": table,
                "fields_to_create": fields,
                "missing_mandatory_fields": missing,
                "message": (
                    f"Missing mandatory fields: {', '.join(missing)}. "
                    "These fields are required — please provide them before confirming."
                    if missing else
                    f"Ready to create a new {table} record with the above fields. "
                    "Call again with confirm=True to proceed."
                ),
            }

        # confirm=True — block if mandatory fields are missing
        if missing:
            return {
                "error": "Cannot create record — mandatory fields missing",
                "missing_mandatory_fields": missing,
                "message": (
                    f"The following fields are required but were not provided: "
                    f"{', '.join(missing)}. Please include them and try again."
                ),
            }

        # Execute the write
        raw_result = client_create(table, fields)
        shaped_result = shape_record(raw_result, table)
        number = shaped_result.get("number") or raw_result.get("number", {}).get("value", "unknown")
        sys_id = raw_result.get("sys_id", {}).get("value", "") if isinstance(raw_result.get("sys_id"), dict) else raw_result.get("sys_id", "")

        log_write("create", table, number, fields)
        log_tool_call("create_record", params, f"created {number}")

        return {
            "created": True,
            "table": table,
            "number": number,
            "sys_id": sys_id,
            "record": shaped_result,
        }

    except Exception as e:
        log_error("create_record", params, str(e))
        return {"error": str(e), "table": table}


# ── update_record ─────────────────────────────────────────────────────────────

@mcp.tool()
def update_record(
        table: str,
        identifier: str,
        fields: dict,
        confirm: bool = False,
) -> dict:
    """
    Update a single existing ServiceNow record.

    IMPORTANT — TWO-STEP PATTERN. Always call this tool TWICE:
      Step 1: confirm=False (default) — fetches the current record and shows exactly
              which fields will change (before → after). Present this diff to the user
              and ask "Shall I go ahead?"
      Step 2: confirm=True — only after the user explicitly says yes.

    Never skip the preview step. Never call with confirm=True on the first attempt.
    This tool updates ONE record only. Never call it in a loop to update multiple records.
    For bulk updates use bulk_preview + bulk_execute (v0.3).

    Parameters:
    - table:      ServiceNow table name (e.g. "incident", "problem", "change_request")
    - identifier: Record number (e.g. "INC0001234") or 32-character sys_id hex string.
    - fields:     Dict of field names and values to change. Only include fields you want
                  to modify — unspecified fields are not touched (PATCH semantics).
                  CRITICAL — field values must be RAW ServiceNow codes, not display labels.
                  For unfamiliar tables or fields, call describe_table first to check valid values.

                  Common values for standard ITSM tables:
                    priority:  "1"=Critical  "2"=High  "3"=Moderate  "4"=Low
                    impact:    "1"=High  "2"=Medium  "3"=Low
                    urgency:   "1"=High  "2"=Medium  "3"=Low
                    state (incident): "1"=New "2"=In Progress "6"=Resolved "7"=Closed
                    state (problem):  "101"=New "106"=Resolved "107"=Closed
                    state (change):   "-5"=New "-1"=Implement "3"=Closed
                  Reference fields (assigned_to, caller_id, assignment_group) accept
                  sys_id or user_name/name — ServiceNow resolves them.
                  For custom tables: call describe_table to discover field names and types.
    - confirm:    False = preview only (default). True = execute the update.

    Returns on preview (confirm=False):
      {"preview": true, "record": "INC0001234", "changes": [{"field":..,"from":..,"to":..}],
       "unchanged": [...], "new_fields": [...]}
    Returns on success (confirm=True):
      {"updated": true, "record": "INC0001234", "changes": [...], "result": {shaped record}}
    """
    params = {"table": table, "identifier": identifier, "fields": fields, "confirm": confirm}

    try:
        # Resolve identifier to a raw record (need both sys_id and current shaped state)
        is_sys_id = len(identifier) == 32 and all(c in "0123456789abcdef" for c in identifier.lower())
        if is_sys_id:
            raw_current = get_record_by_sys_id(table, identifier)
        else:
            raw_current = get_record_by_number(table, identifier)

        # Shape current record for diff display and for extracting sys_id
        shaped_current = shape_record(raw_current, table)
        number = shaped_current.get("number", identifier)

        # Extract sys_id from raw response for the actual PATCH call.
        # With sysparm_display_value=all, sys_id is {"display_value": "...", "value": "..."}
        sys_id_field = raw_current.get("sys_id")
        if isinstance(sys_id_field, dict):
            sys_id = sys_id_field.get("value", "")
        else:
            sys_id = sys_id_field or ""

        if not sys_id:
            raise RuntimeError(
                f"Could not extract sys_id from {identifier}. "
                "Cannot perform update without a valid sys_id."
            )

        # Generate diff — what will change, what won't
        diff = diff_fields(shaped_current, fields)

        if not confirm:
            # Preview mode — return diff, write nothing
            log_tool_call("update_record:preview", params, f"preview for {number}")
            message = (
                f"No fields will change on {number}."
                if not diff["changes"] and not diff["new_fields"]
                else f"Ready to update {number}. Call again with confirm=True to proceed."
            )
            return {
                "preview": True,
                "table": table,
                "record": number,
                "changes": diff["changes"],
                "unchanged": diff["unchanged"],
                "new_fields": diff["new_fields"],
                "message": message,
            }

        # confirm=True — execute the write
        if not fields:
            return {"error": "No fields provided to update.", "record": number}

        raw_result = client_update(table, sys_id, fields)
        shaped_result = shape_record(raw_result, table)

        log_write("update", table, number, fields, diff)
        log_tool_call("update_record", params, f"updated {number} (sys_id={sys_id}), {len(diff['changes'])} field(s) changed")

        return {
            "updated": True,
            "table": table,
            "record": number,
            "changes": diff["changes"],
            "result": shaped_result,
        }

    except Exception as e:
        log_error("update_record", params, str(e))
        return {"error": str(e)}


# ── bulk_preview ──────────────────────────────────────────────────────────────

@mcp.tool()
def bulk_preview(
        table: str,
        filters: str,
        fields_to_set: dict,
) -> dict:
    """
    Preview a bulk update operation and generate a session token required by bulk_execute.

    ALWAYS call this tool before bulk_execute. bulk_execute requires the token
    returned here and will refuse to run without it.

    STOP after calling this tool. Do not call bulk_execute automatically.
    Present the count, sample, and token to the user and wait for their explicit
    instruction to proceed. The user must say "yes", "execute", "go ahead" or
    similar before you call bulk_execute.

    What this tool does:
    1. Counts how many records match the filter
    2. Refuses if count exceeds 500 (hard limit — use a tighter filter)
    3. Returns a sample of up to 5 matching records showing before → after for each field
    4. Generates a session token valid for 5 minutes

    Parameters:
    - table:         ServiceNow table name (e.g. "incident", "problem")
    - filters:       Encoded ServiceNow query string to select records to update.
                     Be specific — this will affect every matching record.
                     Examples: "state=1^priority=3" — all New Moderate incidents
                               "assignment_group=IT^state=2" — all In Progress for IT group
    - fields_to_set: Dict of {field_name: raw_value} to set on every matching record.
                     Same raw code rules as update_record.
                     Common values — priority: "1"=Critical "2"=High "3"=Moderate "4"=Low
                     state (incident): "1"=New "2"=In Progress "6"=Resolved "7"=Closed

    Returns:
      {
        "count": 42,
        "sample": [{shaped record before}, ...],  # up to 5 records
        "fields_to_set": {...},
        "token": "uuid-string",                   # pass this to bulk_execute
        "expires_at": "2026-05-31T14:32:00",
        "message": "Ready to update 42 records..."
      }
    Or if count > 500:
      {"error": "...", "count": 612, "limit": 500}
    """
    params = {"table": table, "filters": filters, "fields_to_set": fields_to_set}
    try:
        count, sample_raw = client_bulk_query(table, filters)

        if count > 500:
            return {
                "error": (
                    f"This filter matches {count} records, which exceeds the 500-record "
                    f"safety limit. Please use a tighter filter and try again."
                ),
                "count": count,
                "limit": 500,
            }

        if count == 0:
            return {
                "error": "No records match this filter. Nothing to update.",
                "count": 0,
            }

        # Shape sample and generate before→after diff for each record
        from nowlink.shaper import shape_records
        from nowlink.safety import diff_fields
        sample_shaped = shape_records(sample_raw, table)
        sample_with_diff = []
        for record in sample_shaped:
            diff = diff_fields(record, fields_to_set)
            sample_with_diff.append({
                "number": record.get("number", "unknown"),
                "short_description": record.get("short_description", ""),
                "changes": diff["changes"],
                "unchanged": diff["unchanged"],
            })

        # Generate session token — stores the full preview state
        token = str(uuid.uuid4())
        expires_at = datetime.now() + timedelta(minutes=BULK_TOKEN_TTL_MINUTES)
        _bulk_tokens[token] = {
            "table": table,
            "filters": filters,
            "fields_to_set": fields_to_set,
            "count": count,
            "expires_at": expires_at,
        }

        log_tool_call("bulk_preview", params, f"{count} records matched, token {token[:8]}... generated")
        return {
            "count": count,
            "sample": sample_with_diff,
            "fields_to_set": fields_to_set,
            "token": token,
            "expires_at": expires_at.strftime("%Y-%m-%dT%H:%M:%S"),
            "message": (
                f"{count} record(s) on {table} will be updated. "
                f"Fields to set: {fields_to_set}. "
                f"Sample of first {len(sample_with_diff)} records shown above with before→after changes. "
                f"Call bulk_execute with the token to proceed. "
                f"Token expires in {BULK_TOKEN_TTL_MINUTES} minutes."
            ),
        }

    except Exception as e:
        log_error("bulk_preview", params, str(e))
        return {"error": str(e)}


# ── bulk_execute ──────────────────────────────────────────────────────────────

@mcp.tool()
def bulk_execute(
        token: str,
) -> dict:
    """
    Execute a bulk update using a token generated by bulk_preview.

    WORKFLOW — ALWAYS follow this exact sequence:
      1. Call bulk_preview → show the count and sample to the user
      2. STOP and ask the user: "Shall I go ahead and update all X records?"
      3. Wait for explicit user confirmation (yes/no)
      4. Only if user says yes: call bulk_execute with the token

    NEVER call bulk_execute immediately after bulk_preview without user confirmation.
    NEVER call bulk_execute if the user has not explicitly approved the operation.

    Only call this tool when the user has explicitly confirmed they want to proceed
    after seeing the bulk_preview results. Explicit confirmation means the user
    has said "yes", "execute", "go ahead", "do it" or similar in response to
    the preview. A general request like "bulk update all incidents" is NOT
    confirmation — you must show the preview first and wait for approval.

    Parameters:
    - token: The token string returned by bulk_preview. Required.
             The token encodes the table, filter, fields, and record count.
             It cannot be modified — what was previewed is what gets executed.
             Token expires 5 minutes after bulk_preview was called.

    Returns on success:
      {"updated": N, "failed": M, "failures": [...], "message": "..."}
    On token not found or expired:
      {"error": "Token not found..." / "Token expired..."}
    """
    params = {"token": token[:8] + "..."}
    try:
        # Validate token exists and hasn't expired
        stored = _bulk_tokens.get(token)
        if not stored:
            return {
                "error": "Token not found. Call bulk_preview first to generate a valid token.",
            }
        if datetime.now() > stored["expires_at"]:
            del _bulk_tokens[token]
            return {
                "error": (
                    f"Token expired ({BULK_TOKEN_TTL_MINUTES} minute limit). "
                    "Call bulk_preview again to generate a fresh token."
                ),
            }

        table = stored["table"]
        filters = stored["filters"]
        fields_to_set = stored["fields_to_set"]
        preview_count = stored["count"]

        # Re-count before executing — records may have changed since preview
        current_count, _ = client_bulk_query(table, filters)
        if current_count > 500:
            del _bulk_tokens[token]
            return {
                "error": (
                    f"Record count has changed since preview: now {current_count} records "
                    f"(was {preview_count}), exceeding the 500-record limit. "
                    "Call bulk_preview again with a tighter filter."
                ),
                "count": current_count,
            }

        # Fetch sys_ids for all matching records
        all_records = bulk_fetch_sys_ids(table, filters, limit=500)
        sys_ids = []
        for record in all_records:
            sys_id_field = record.get("sys_id")
            sys_id = sys_id_field if isinstance(sys_id_field, str) else (sys_id_field or "")
            if sys_id:
                sys_ids.append(sys_id)

        if not sys_ids:
            del _bulk_tokens[token]
            return {"error": "No records found to update — filter may have changed since preview."}

        # Fire the batch — sub-request statuses ignored, outcome verified by re-count
        client_bulk_update(table, sys_ids, fields_to_set)

        # Post-execution re-count — this is the source of truth.
        # Batch sub-request status codes are unreliable on PDI.
        try:
            remaining_count, _ = client_bulk_query(table, filters)
        except Exception:
            remaining_count = None

        actually_updated = len(sys_ids) - (remaining_count or 0)

        log_write("update", table, f"bulk:{filters}:{actually_updated}records", fields_to_set)
        del _bulk_tokens[token]

        log_tool_call(
            "bulk_execute", params,
            f"{actually_updated} confirmed updated on {table}, {remaining_count} remaining"
        )
        return {
            "updated": actually_updated,
            "remaining": remaining_count,
            "message": (
                f"Bulk update complete: {actually_updated} of {len(sys_ids)} record(s) updated on {table}. "
                + ("All records updated successfully." if remaining_count == 0
                   else f"{remaining_count} record(s) still match the filter — run bulk_preview again to update them.")
            ),
        }

    except Exception as e:
        log_error("bulk_execute", params, str(e))
        return {"error": str(e)}


# ── get_write_log ─────────────────────────────────────────────────────────────

@mcp.tool()
def get_write_log(
        date: str = "",
        table: str = "",
        record: str = "",
        limit: int = 20,
) -> dict:
    """
    Read NowLink's write audit log — every create and update NowLink has performed.

    Use this tool when:
    - The user asks what NowLink has changed recently
    - After a bulk_execute, to confirm what was updated
    - To audit a specific record's change history via NowLink

    Parameters:
    - date:   Date to read log for, YYYY-MM-DD format. Defaults to today.
    - table:  Filter by table name (e.g. "incident"). Optional.
    - record: Filter by record number (e.g. "INC0001234"). Optional.
    - limit:  Maximum entries to return. Default 20.

    Returns a list of write entries, most recent first:
      [{"ts": ..., "op": "update", "table": "incident", "record": "INC0001234",
        "fields": {...}, "changes": [...]}]
    """
    params = {"date": date, "table": table, "record": record, "limit": limit}
    try:
        from pathlib import Path
        import json

        log_date = date if date else datetime.now().strftime("%Y-%m-%d")
        log_file = Path.home() / ".nowlink" / "logs" / f"writes-{log_date}.log"

        if not log_file.exists():
            return {
                "date": log_date,
                "entries": [],
                "message": f"No write log found for {log_date}.",
            }

        entries = []
        with open(log_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Apply filters
                if table and entry.get("table") != table:
                    continue
                if record and entry.get("record") != record:
                    continue

                entries.append(entry)

        # Most recent first, apply limit
        entries.reverse()
        entries = entries[:limit]

        log_tool_call("get_write_log", params, f"{len(entries)} entries returned for {log_date}")
        return {
            "date": log_date,
            "count": len(entries),
            "entries": entries,
        }

    except Exception as e:
        log_error("get_write_log", params, str(e))
        return {"error": str(e)}
